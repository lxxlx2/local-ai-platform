import subprocess
import uuid
from pathlib import Path

import pytest

from local_ai_control.services.models import QWEN38
from local_ai_control.services.supervisor import (
    LocalProducerExecutionUncertain,
    LocalQwenCodexRunner,
    LocalWorktreeCodexTaskSpec,
    LocalWorktreeSupervisorRepository,
    PersistedCodexStageRunner,
    StageContext,
    StageResultStatus,
    WorkflowStage,
    create_local_qwen_job,
)


def git(root, *args):
    return subprocess.run(
        ("git", *args), cwd=root, capture_output=True, text=True,
        shell=False, timeout=10, check=True,
    ).stdout.strip()


def make_feature_repo(tmp_path, branch="feat/local-qwen-test"):
    root = tmp_path / "repo"
    (root / "control-plane/src").mkdir(parents=True)
    (root / "control-plane/tests").mkdir(parents=True)
    (root / "control-plane/scripts").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "control-plane/src/app.py").write_text("def value():\n    return 1\n")
    (root / "control-plane/tests/test_app.py").write_text(
        "from app import value\n\ndef test_value():\n    assert value() == 1\n"
    )
    launcher = root / "control-plane/scripts/run-codex-qwen-local.sh"
    launcher.write_text("#!/bin/zsh\nexit 0\n")
    launcher.chmod(0o755)
    (root / "docs/README.md").write_text("local test\n")
    git(root, "init", "-b", branch)
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    return root


def task_spec(root):
    return LocalWorktreeCodexTaskSpec(
        root,
        (root / "control-plane", root / "docs"),
        "Inspect the implementation, run tests, and report success.",
        "LOW",
        60,
        "CODE",
        {"type": "object"},
    )


def healthy_bridge():
    return {
        "status": "healthy",
        "backend": QWEN38.model_id,
        "tool": "exec_command",
    }


class Completed:
    returncode = 0


def test_local_qwen_runner_is_disabled_by_default(tmp_path):
    root = make_feature_repo(tmp_path)
    calls = []

    def should_not_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("disabled runner must not spawn Codex")

    runner = LocalQwenCodexRunner(command_runner=should_not_run)
    result = runner.run_task(task_spec(root), str(uuid.uuid4()))
    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "LOCAL_QWEN_PRODUCER_DISABLED"
    assert calls == []


def test_local_qwen_runner_feature_worktree_success_uses_safe_command(tmp_path):
    root = make_feature_repo(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    runner = LocalQwenCodexRunner(
        enabled=True,
        health_probe=healthy_bridge,
        command_runner=fake_run,
    )
    result = runner.run_task(task_spec(root), str(uuid.uuid4()))
    assert result.status is StageResultStatus.PASS
    assert result.metrics["network_access"] is False
    assert result.metrics["git_mutation_authority"] is False
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == (
        "/bin/zsh",
        str(root / "control-plane/scripts/run-codex-qwen-local.sh"),
        str(root),
    )
    assert command[3:6] == ("exec", "--json", "--ephemeral")
    assert kwargs["shell"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert not any(key.upper().endswith(("TOKEN", "SECRET", "PASSWORD")) for key in kwargs["env"])


def test_local_qwen_runner_rejects_main_before_health_or_process(tmp_path):
    root = make_feature_repo(tmp_path, branch="main")
    health_calls = []
    process_calls = []

    runner = LocalQwenCodexRunner(
        enabled=True,
        health_probe=lambda: health_calls.append(True) or healthy_bridge(),
        command_runner=lambda *a, **k: process_calls.append(True) or Completed(),
    )
    base = task_spec(root)
    result = runner.run_task(base, str(uuid.uuid4()))
    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "LOCAL_QWEN_WORKSPACE_DENIED"
    assert health_calls == [] and process_calls == []


def test_local_qwen_bridge_identity_mismatch_blocks_process(tmp_path):
    root = make_feature_repo(tmp_path)
    process_calls = []
    runner = LocalQwenCodexRunner(
        enabled=True,
        health_probe=lambda: {"status": "healthy", "backend": "wrong", "tool": "exec_command"},
        command_runner=lambda *a, **k: process_calls.append(True) or Completed(),
    )
    result = runner.run_task(task_spec(root), str(uuid.uuid4()))
    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "LOCAL_QWEN_BRIDGE_IDENTITY_MISMATCH"
    assert process_calls == []


def test_local_qwen_timeout_is_execution_uncertainty(tmp_path):
    root = make_feature_repo(tmp_path)

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    runner = LocalQwenCodexRunner(
        enabled=True,
        health_probe=healthy_bridge,
        command_runner=timeout,
    )
    with pytest.raises(LocalProducerExecutionUncertain, match="LOCAL_QWEN_CODEX_TIMEOUT"):
        runner.run_task(task_spec(root), str(uuid.uuid4()))


def prepare_durable_producer(tmp_path, command_runner):
    root = make_feature_repo(tmp_path)
    repo = LocalWorktreeSupervisorRepository(root, tmp_path / "runtime/supervisor.db")
    repo.migrate()
    job, unit = create_local_qwen_job(
        repo,
        title="local producer",
        owner_id="owner",
        task_prompt="Inspect the code and complete the requested local change.",
        job_id="local-job",
    )
    job = repo.update_job(job.job_id, current_stage=WorkflowStage.PRODUCER)
    started = repo.begin_stage(job)
    assert started is not None
    run_id, attempt, key = started
    running = repo.get_job(job.job_id)
    context = StageContext(running, WorkflowStage.PRODUCER, attempt, key, 60, repo)
    inner = LocalQwenCodexRunner(
        enabled=True,
        health_probe=healthy_bridge,
        command_runner=command_runner,
    )
    return root, repo, unit, context, PersistedCodexStageRunner(inner), run_id


def test_persisted_local_qwen_execution_is_confirmed_in_durable_ledger(tmp_path):
    root, repo, unit, context, runner, _run_id = prepare_durable_producer(
        tmp_path, lambda *a, **k: Completed()
    )
    result = runner.run(context)
    assert result.status is StageResultStatus.PASS
    row = repo.db.execute(
        "SELECT * FROM supervisor_executions WHERE job_id=?", (context.job.job_id,)
    ).fetchone()
    assert row["provider"] == "LocalQwenCodexRunner"
    assert row["work_unit_id"] == unit.work_unit_id
    assert row["completion_status"] == "COMPLETED_CONFIRMED"
    assert repo.active_mutation_fence() is None
    assert git(root, "branch", "--show-current") == "feat/local-qwen-test"
    repo.close()


def test_uncertain_local_qwen_execution_creates_mutation_fence(tmp_path):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    _root, repo, unit, context, runner, _run_id = prepare_durable_producer(tmp_path, timeout)
    with pytest.raises(LocalProducerExecutionUncertain):
        runner.run(context)
    fence = repo.active_mutation_fence()
    assert fence is not None
    assert fence["job_id"] == context.job.job_id
    assert fence["work_unit_id"] == unit.work_unit_id
    assert fence["reason"] == "EXTERNAL_EXECUTION_UNCERTAIN"
    repo.close()


def test_local_repository_binds_job_and_work_unit_to_exact_feature_root(tmp_path):
    root = make_feature_repo(tmp_path)
    repo = LocalWorktreeSupervisorRepository(root, tmp_path / "runtime/supervisor.db")
    repo.migrate()
    job = repo.create_job("scope", "owner", project_scope=str(root), job_id="scope-job")
    assert Path(job.project_scope) == root.resolve()
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(PermissionError):
        repo.create_job("wrong", "owner", project_scope=str(other), job_id="wrong-job")
    repo.close()
