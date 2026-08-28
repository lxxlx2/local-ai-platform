import json
import inspect
import signal
import subprocess
import sys
import threading
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


class FakeProcess:
    next_pid=51000
    def __init__(self,*,stdout="",stderr="",returncode=0,timeout=False,ignore_term=False):
        self.pid=FakeProcess.next_pid; FakeProcess.next_pid+=1
        self.stdout=stdout; self.stderr=stderr; self.final_returncode=returncode
        self.returncode=None; self.timeout=timeout; self.ignore_term=ignore_term
        self.signals=[]; self.wait_calls=0
    def communicate(self,timeout=None):
        if self.timeout and self.returncode is None:
            raise subprocess.TimeoutExpired(("fake",),timeout,output=self.stdout,stderr=self.stderr)
        if self.returncode is None: self.returncode=self.final_returncode
        return self.stdout,self.stderr
    def poll(self): return self.returncode
    def wait(self,timeout=None):
        self.wait_calls+=1
        if self.returncode is None: raise subprocess.TimeoutExpired(("fake",),timeout)
        return self.returncode
    def terminate(self): self.returncode=-15
    def kill(self): self.returncode=-9
    def signal_group(self,signal_number):
        self.signals.append(signal_number)
        if signal_number==9 or not self.ignore_term: self.returncode=-signal_number


def fake_process_kwargs(process,calls=None):
    def factory(command,**kwargs):
        if calls is not None: calls.append((command,kwargs))
        return process
    return {
        "popen_factory":factory,
        "pgid_resolver":lambda pid:pid,
        "group_signaler":lambda pgid,sig:process.signal_group(sig),
        "cancel_wait_seconds":0.05,
    }


def test_local_qwen_runner_is_disabled_by_default(tmp_path):
    root = make_feature_repo(tmp_path)
    calls = []

    def should_not_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("disabled runner must not spawn Codex")

    runner = LocalQwenCodexRunner(popen_factory=should_not_run,trace_root=tmp_path/"traces")
    result = runner.run_task(task_spec(root), str(uuid.uuid4()))
    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "LOCAL_QWEN_PRODUCER_DISABLED"
    assert calls == []


def test_local_qwen_runner_feature_worktree_success_uses_safe_command(tmp_path):
    root = make_feature_repo(tmp_path)
    calls = []

    runner = LocalQwenCodexRunner(
        enabled=True,
        health_probe=healthy_bridge,
        **fake_process_kwargs(FakeProcess(),calls),
        trace_root=tmp_path/"traces",
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
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert kwargs["text"] is True
    assert kwargs["start_new_session"] is True
    assert not any(key.upper().endswith(("TOKEN", "SECRET", "PASSWORD")) for key in kwargs["env"])


def test_local_qwen_runner_rejects_main_before_health_or_process(tmp_path):
    root = make_feature_repo(tmp_path, branch="main")
    health_calls = []
    process_calls = []

    runner = LocalQwenCodexRunner(
        enabled=True,
        health_probe=lambda: health_calls.append(True) or healthy_bridge(),
        popen_factory=lambda *a, **k: process_calls.append(True) or FakeProcess(),
        trace_root=tmp_path/"traces",
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
        popen_factory=lambda *a, **k: process_calls.append(True) or FakeProcess(),
        trace_root=tmp_path/"traces",
    )
    result = runner.run_task(task_spec(root), str(uuid.uuid4()))
    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "LOCAL_QWEN_BRIDGE_IDENTITY_MISMATCH"
    assert process_calls == []


def test_local_qwen_timeout_is_execution_uncertainty(tmp_path):
    root = make_feature_repo(tmp_path)

    process=FakeProcess(timeout=True)
    runner = LocalQwenCodexRunner(
        enabled=True,
        health_probe=healthy_bridge,
        **fake_process_kwargs(process),
        trace_root=tmp_path/"traces",
    )
    with pytest.raises(LocalProducerExecutionUncertain, match="LOCAL_QWEN_CODEX_TIMEOUT_TERMINATED"):
        runner.run_task(task_spec(root), str(uuid.uuid4()))
    assert process.signals==[15] and process.poll() is not None


def test_execution_trace_keeps_only_bounded_structural_events(tmp_path):
    root=make_feature_repo(tmp_path)
    execution_id=str(uuid.uuid4())
    process=FakeProcess(stdout=(
            json.dumps({"type":"item.started","item":{"type":"command_execution","command":"SECRET COMMAND"}})+"\n"
            +json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"PRIVATE MODEL REPLY"}})+"\n"
            +"malformed raw tool output SECRET_VALUE\n"
        ),stderr="private stderr body SECRET_STDERR\n")
    result=LocalQwenCodexRunner(
        enabled=True,health_probe=healthy_bridge,**fake_process_kwargs(process),
        trace_root=tmp_path/"runtime"/"executions",
    ).run_task(task_spec(root),execution_id)
    trace_path=tmp_path/"runtime"/"executions"/f"{execution_id}.json"
    trace=json.loads(trace_path.read_text())
    encoded=trace_path.read_text()
    assert result.status is StageResultStatus.PASS and result.metrics["trace_written"] is True
    assert trace["json_event_count"]==2 and trace["malformed_json_line_count"]==1
    assert trace["command_execution_count"]==1 and trace["agent_message_count"]==1
    assert not any(value in encoded for value in ("SECRET COMMAND","PRIVATE MODEL REPLY","SECRET_VALUE","SECRET_STDERR"))
    assert (trace_path.parent.stat().st_mode & 0o777)==0o700
    assert (trace_path.stat().st_mode & 0o777)==0o600


def test_timeout_trace_summarizes_partial_stream_without_raw_content(tmp_path):
    root=make_feature_repo(tmp_path)
    execution_id=str(uuid.uuid4())
    partial=(json.dumps({"type":"item.started","item":{"type":"command_execution","command":"do not persist"}})+"\n").encode()
    process=FakeProcess(stdout=partial,stderr=b"private error",timeout=True)
    runner=LocalQwenCodexRunner(
        enabled=True,health_probe=healthy_bridge,**fake_process_kwargs(process),
        trace_root=tmp_path/"runtime"/"executions",
    )
    with pytest.raises(LocalProducerExecutionUncertain):
        runner.run_task(task_spec(root),execution_id)
    path=tmp_path/"runtime"/"executions"/f"{execution_id}.json"
    trace=json.loads(path.read_text())
    assert trace["timed_out"] is True and trace["command_execution_count"]==1
    assert "do not persist" not in path.read_text() and "private error" not in path.read_text()


def test_exact_execution_cancel_stops_owned_group_and_not_unrelated(tmp_path):
    owned=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],start_new_session=True)
    unrelated=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],start_new_session=True)
    execution_id=str(uuid.uuid4())
    runner=LocalQwenCodexRunner(trace_root=tmp_path/"traces",cancel_wait_seconds=1)
    try:
        runner._register(execution_id,owned)
        assert runner.cancel(str(uuid.uuid4())) is False
        assert runner.cancel(execution_id,reason="test") is True
        assert owned.poll() is not None
        assert unrelated.poll() is None
        assert runner._executions=={}
        assert runner.cancel(execution_id) is False
    finally:
        if owned.poll() is None:
            owned.terminate(); owned.wait(timeout=5)
        if unrelated.poll() is None:
            unrelated.terminate(); unrelated.wait(timeout=5)


def test_cancel_escalates_exact_group_after_bounded_term_timeout(tmp_path):
    process=FakeProcess(timeout=True,ignore_term=True)
    execution_id=str(uuid.uuid4())
    runner=LocalQwenCodexRunner(trace_root=tmp_path/"traces",**fake_process_kwargs(process))
    runner._register(execution_id,process)
    assert runner.cancel(execution_id) is True
    assert process.signals==[signal.SIGTERM,signal.SIGKILL]
    assert process.wait_calls==2 and runner._executions=={}


def test_already_completed_owned_process_is_reaped_without_signal(tmp_path):
    process=FakeProcess(); process.returncode=0
    execution_id=str(uuid.uuid4())
    runner=LocalQwenCodexRunner(trace_root=tmp_path/"traces",**fake_process_kwargs(process))
    runner._register(execution_id,process)
    assert runner.cancel(execution_id) is False
    assert process.signals==[] and runner._executions=={}


def test_registration_failure_reaps_exact_spawned_child(tmp_path):
    root=make_feature_repo(tmp_path)
    process=FakeProcess(timeout=True)
    runner=LocalQwenCodexRunner(
        enabled=True,health_probe=healthy_bridge,popen_factory=lambda *a,**k:process,
        pgid_resolver=lambda pid:(_ for _ in ()).throw(OSError("metadata unavailable")),
        trace_root=tmp_path/"traces",
    )
    result=runner.run_task(task_spec(root),str(uuid.uuid4()))
    assert result.status is StageResultStatus.BLOCKED
    assert process.poll()==-15 and process.wait_calls==1


def test_concurrent_cancel_only_one_caller_owns_termination(tmp_path):
    process=FakeProcess(timeout=True)
    execution_id=str(uuid.uuid4())
    runner=LocalQwenCodexRunner(trace_root=tmp_path/"traces",**fake_process_kwargs(process))
    runner._register(execution_id,process)
    results=[]
    threads=[threading.Thread(target=lambda:results.append(runner.cancel(execution_id))) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert results.count(True)==1
    assert process.signals==[signal.SIGTERM]
    assert runner._executions=={}


def test_cancel_implementation_has_no_broad_process_scan():
    source=inspect.getsource(LocalQwenCodexRunner)
    assert not any(token in source for token in ("pkill","killall","pgrep"))


def prepare_durable_producer(tmp_path, process):
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
        **fake_process_kwargs(process),
        trace_root=tmp_path/"traces",
    )
    return root, repo, unit, context, PersistedCodexStageRunner(inner), run_id


def test_persisted_local_qwen_execution_is_confirmed_in_durable_ledger(tmp_path):
    root, repo, unit, context, runner, _run_id = prepare_durable_producer(
        tmp_path, FakeProcess()
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
    _root, repo, unit, context, runner, _run_id = prepare_durable_producer(tmp_path, FakeProcess(timeout=True))
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
