from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

from local_ai_control.services.codex_availability import CodexAvailabilityEvidence
from local_ai_control.services.codex_failover_adapter import (
    CodexDesktopLocalAdapter,
    ProviderAwareCodexStageRunner,
)
from local_ai_control.services.codex_quota_guard import CodexQuotaSnapshot
from local_ai_control.services.models import QWEN38
from local_ai_control.services.provider_failover import (
    AvailabilityEvidenceSource,
    LocalFailoverPreflight,
    ProviderFailoverController,
)
from local_ai_control.services.supervisor import (
    LocalWorktreeSupervisorRepository,
    StageResult,
    StageResultStatus,
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=root, capture_output=True, text=True,
        shell=False, timeout=10, check=True,
    ).stdout.strip()


def local_job(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "control-plane/src").mkdir(parents=True)
    (root / "control-plane/tests").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "control-plane/src/app.py").write_text("VALUE = 1\n")
    (root / "control-plane/tests/test_app.py").write_text("def test_ok(): assert True\n")
    (root / "docs/README.md").write_text("fixture\n")
    git(root, "init", "-b", "feat/adapter-fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    repository = LocalWorktreeSupervisorRepository(root, tmp_path / "supervisor.db")
    repository.migrate()
    job = repository.create_job("Adapter objective", "owner")
    controller = ProviderFailoverController(
        repository,
        LocalFailoverPreflight(
            qwen_health_probe=lambda: {"status": "healthy", "model": QWEN38.model_id},
            bridge_health_probe=lambda: {
                "status": "healthy", "backend": QWEN38.model_id, "tool": "exec_command",
            },
        ),
    )
    controller.register_job(job.job_id)
    controller.failover(
        job.job_id,
        CodexAvailabilityEvidence(snapshot=CodexQuotaSnapshot(100, 1, 0, 2, "plus")),
        evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
        signal_id="adapter-test",
    )
    return root, repository, job


def test_desktop_adapter_uses_private_isolated_codex_home_and_safe_status(tmp_path):
    root, repository, job = local_job(tmp_path)
    fake_global = tmp_path / "global-codex"
    fake_global.mkdir()
    sentinel = fake_global / "config.toml"
    sentinel.write_text("owner-cloud-config\n")
    runtime = tmp_path / "runtime"

    plan = CodexDesktopLocalAdapter(
        repository, runtime_root=runtime, codex_executable="/usr/local/bin/codex",
    ).prepare(job.job_id)

    assert plan.job_id == job.job_id and plan.effective_provider == "LOCAL_QWEN"
    assert plan.workspace_path == str(root) and plan.branch == "feat/adapter-fixture"
    assert plan.cli_argv == ("/usr/local/bin/codex", "-C", str(root))
    assert plan.desktop_argv == ("/usr/local/bin/codex", "app", str(root))
    assert dict(plan.environment_overrides) == {"CODEX_HOME": plan.codex_home}
    assert plan.same_thread_hot_swap_supported is False
    assert plan.desktop_mode == "BEST_EFFORT_NEW_LOCAL_SESSION"
    config = Path(plan.codex_home) / "config.toml"
    config_text = config.read_text()
    assert "http://127.0.0.1:8010/v1" in config_text
    assert "network_access = false" in config_text
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    status = Path(plan.status_path)
    payload = json.loads(status.read_text())
    assert payload["job_id"] == job.job_id and payload["effective_provider"] == "LOCAL_QWEN"
    assert "objective" not in payload and "token" not in status.read_text().lower()
    assert stat.S_IMODE(status.stat().st_mode) == 0o600
    assert stat.S_IMODE(status.parent.stat().st_mode) == 0o700
    assert sentinel.read_text() == "owner-cloud-config\n"
    repository.close()


class FakeStageRunner:
    cancellation_supported = True

    def __init__(self, name):
        self.name = name
        self.calls = []

    def run(self, context):
        self.calls.append(context)
        return StageResult.passed(self.name, metrics={"provider": self.name})

    def cancel(self, execution_id=None, reason=None):
        self.calls.append((execution_id, reason))
        return True


def test_provider_aware_stage_routes_same_context_only_to_local_after_failover(tmp_path):
    _root, repository, job = local_job(tmp_path)
    cloud, local = FakeStageRunner("cloud"), FakeStageRunner("local")
    adapter = ProviderAwareCodexStageRunner(
        repository, cloud_stage_runner=cloud, local_stage_runner=local,
    )
    context = SimpleNamespace(repository=repository, job=job)
    result = adapter.run(context)
    assert result.status is StageResultStatus.PASS
    assert result.metrics["provider"] == "local"
    assert local.calls == [context] and cloud.calls == []
    assert adapter.cancellation_supported
    assert adapter.cancel("execution-id", "operator")
    repository.close()


def test_provider_aware_stage_fails_closed_for_handoff_state_and_foreign_repository(tmp_path):
    _root, repository, job = local_job(tmp_path)
    cloud, local = FakeStageRunner("cloud"), FakeStageRunner("local")
    adapter = ProviderAwareCodexStageRunner(
        repository, cloud_stage_runner=cloud, local_stage_runner=local,
    )
    foreign = SimpleNamespace(repository=object(), job=job)
    assert adapter.run(foreign).error == "PROVIDER_ADAPTER_REPOSITORY_MISMATCH"
    with repository.db:
        repository.db.execute(
            "UPDATE supervisor_provider_state SET state='HANDOFF_PENDING' WHERE job_id=?", (job.job_id,),
        )
    context = SimpleNamespace(repository=repository, job=job)
    result = adapter.run(context)
    assert result.status is StageResultStatus.BLOCKED
    assert not cloud.calls and not local.calls
    repository.close()


def test_launcher_and_qualification_pin_the_same_codex_version():
    root = Path(__file__).parents[1]
    launcher = (root / "scripts/run-codex-qwen-local.sh").read_text()
    qualification = (root / "scripts/qualify-codex-qwen-local.sh").read_text()
    assert '0.148.0' in launcher and '0.148.0' in qualification
    assert '0.146.0' not in qualification
