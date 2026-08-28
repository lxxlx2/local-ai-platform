from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_ai_control.services.codex_availability import CodexAvailabilityEvidence
from local_ai_control.services.codex_quota_guard import CodexQuotaSnapshot
from local_ai_control.services.models import QWEN38
from local_ai_control.services.provider_failover import (
    AvailabilityEvidenceSource,
    FailoverDenied,
    LocalFailoverPreflight,
    ProviderFailoverController,
    ProviderIdentity,
    ProviderState,
)
from local_ai_control.services.supervisor import (
    JobStatus,
    LocalWorktreeSupervisorRepository,
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=root, capture_output=True, text=True,
        shell=False, timeout=10, check=True,
    ).stdout.strip()


def feature_repo(tmp_path: Path, branch: str = "feat/failover-fixture") -> Path:
    root = tmp_path / "repo"
    (root / "control-plane/src").mkdir(parents=True)
    (root / "control-plane/tests").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "control-plane/src/app.py").write_text("VALUE = 1\n")
    (root / "control-plane/tests/test_app.py").write_text("def test_ok():\n    assert True\n")
    (root / "docs/README.md").write_text("fixture\n")
    git(root, "init", "-b", branch)
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    return root


def healthy_qwen():
    return {"status": "healthy", "model": QWEN38.model_id}


def healthy_bridge():
    return {"status": "healthy", "backend": QWEN38.model_id, "tool": "exec_command"}


def setup(tmp_path: Path, *, qwen=healthy_qwen, bridge=healthy_bridge):
    root = feature_repo(tmp_path)
    repository = LocalWorktreeSupervisorRepository(root, tmp_path / "supervisor.db")
    repository.migrate()
    job = repository.create_job("Continue the approved feature safely", "owner")
    preflight = LocalFailoverPreflight(qwen_health_probe=qwen, bridge_health_probe=bridge)
    controller = ProviderFailoverController(repository, preflight)
    controller.register_job(job.job_id)
    return root, repository, job, controller


def exhausted():
    return CodexAvailabilityEvidence(
        snapshot=CodexQuotaSnapshot(100, 1000, 10, 2000, "plus")
    )


def available():
    return CodexAvailabilityEvidence(
        snapshot=CodexQuotaSnapshot(10, 1000, 10, 2000, "plus")
    )


def test_controlled_quota_failover_preserves_same_job_workspace_diff_and_history(tmp_path):
    root, repository, job, controller = setup(tmp_path)
    (root / "control-plane/src/app.py").write_text("VALUE = 2\n")

    result = controller.failover(
        job.job_id, exhausted(),
        evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
        signal_id="quota-window-1000",
    )

    assert result["job_id"] == job.job_id
    assert result["workspace_path"] == str(root)
    assert result["branch"] == "feat/failover-fixture"
    assert result["current_provider"] == ProviderIdentity.LOCAL_QWEN.value
    assert result["state"] == ProviderState.CONTINUE_SAME_JOB.value
    history = repository.provider_history(job.job_id)
    assert [entry["to_state"] for entry in history] == [
        "HANDOFF_PENDING", "LOCAL_PREFLIGHT", "LOCAL_QWEN", "CONTINUE_SAME_JOB",
    ]
    assert all(entry["candidate_diff_sha256"] == history[0]["candidate_diff_sha256"] for entry in history)
    assert history[0]["candidate_diff_sha256"] != "0" * 64
    assert repository.get_job(job.job_id).title == job.title
    repository.close()


def test_repeated_signal_is_idempotent_and_does_not_create_a_new_job(tmp_path):
    _root, repository, job, controller = setup(tmp_path)
    first = controller.failover(
        job.job_id, exhausted(),
        evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
        signal_id="same-signal",
    )
    second = controller.failover(
        job.job_id, exhausted(),
        evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
        signal_id="same-signal",
    )
    assert first == second
    assert len(repository.provider_history(job.job_id)) == 4
    assert len(repository.list_jobs()) == 1
    repository.close()


@pytest.mark.parametrize(
    ("evidence", "source"),
    [
        (CodexAvailabilityEvidence(error_text="ambiguous failure"), AvailabilityEvidenceSource.ACTIVE_REQUEST_ERROR),
        (CodexAvailabilityEvidence(error_text="HTTP 429"), AvailabilityEvidenceSource.PROVIDER_PROBE),
        (available(), AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY),
    ],
)
def test_unknown_available_and_unattributed_rate_limit_fail_closed(tmp_path, evidence, source):
    _root, repository, job, controller = setup(tmp_path)
    with pytest.raises(FailoverDenied):
        controller.failover(job.job_id, evidence, evidence_source=source, signal_id="denied")
    state = repository.provider_state(job.job_id)
    assert state["state"] == "CLOUD_CODEX"
    assert repository.provider_history(job.job_id) == []
    repository.close()


def test_active_request_rate_limit_and_provider_probe_unavailable_are_allowed(tmp_path):
    _root, repository, job, controller = setup(tmp_path)
    controller.failover(
        job.job_id, CodexAvailabilityEvidence(error_text="HTTP 429"),
        evidence_source=AvailabilityEvidenceSource.ACTIVE_REQUEST_ERROR,
        signal_id="active-429",
    )
    assert repository.provider_state(job.job_id)["current_provider"] == "LOCAL_QWEN"
    repository.close()

    _root, repository, job, controller = setup(tmp_path / "second")
    controller.failover(
        job.job_id, CodexAvailabilityEvidence(error=ConnectionError("down")),
        evidence_source=AvailabilityEvidenceSource.PROVIDER_PROBE,
        signal_id="probe-down",
    )
    assert repository.provider_state(job.job_id)["current_provider"] == "LOCAL_QWEN"
    repository.close()


def test_local_preflight_failure_blocks_durable_job_and_preserves_candidate(tmp_path):
    root, repository, job, controller = setup(
        tmp_path, qwen=lambda: {"status": "healthy", "model": "wrong-model"},
    )
    (root / "control-plane/src/app.py").write_text("VALUE = 3\n")
    result = controller.failover(
        job.job_id, exhausted(),
        evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
        signal_id="blocked-preflight",
    )
    assert result["state"] == "BLOCKED"
    durable_job = repository.get_job(job.job_id)
    assert durable_job.status is JobStatus.BLOCKED
    assert durable_job.resume_state == "LOCAL_FAILOVER_PREFLIGHT_BLOCKED"
    assert (root / "control-plane/src/app.py").read_text() == "VALUE = 3\n"
    assert repository.provider_history(job.job_id)[-1]["to_state"] == "BLOCKED"
    repository.close()


def test_preflight_rejects_branch_change_and_immutable_binding_tampering(tmp_path):
    root, repository, job, controller = setup(tmp_path)
    git(root, "switch", "-c", "feat/other")
    with pytest.raises(FailoverDenied, match="BRANCH_BINDING_MISMATCH"):
        controller.failover(
            job.job_id, exhausted(),
            evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
            signal_id="branch-changed",
        )
    assert repository.provider_state(job.job_id)["state"] == "CLOUD_CODEX"
    with pytest.raises(ValueError):
        repository.initialize_provider_state(
            job.job_id, workspace_path=str(root), branch="feat/other",
            objective_sha256=controller.objective_digest(job),
            current_provider=ProviderIdentity.OPENAI_CODEX, state=ProviderState.CLOUD_CODEX,
        )
    repository.close()


def test_provider_history_is_durable_append_only_and_idempotency_payload_bound(tmp_path):
    root, repository, job, controller = setup(tmp_path)
    controller.failover(
        job.job_id, exhausted(),
        evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
        signal_id="durable",
    )
    expected = repository.provider_history(job.job_id)
    database_path = repository.path
    repository.close()

    reopened = LocalWorktreeSupervisorRepository(root, database_path)
    reopened.migrate()
    assert reopened.provider_history(job.job_id) == expected
    with pytest.raises(Exception, match="append-only"):
        reopened.db.execute(
            "UPDATE supervisor_provider_history SET reason='AVAILABLE' WHERE job_id=?", (job.job_id,),
        )
    reopened.db.rollback()
    with pytest.raises(ValueError, match="payload conflict"):
        reopened.append_provider_transition(
            job.job_id, idempotency_key="failover:durable:continue",
            from_provider="OPENAI_CODEX", to_provider="LOCAL_QWEN",
            from_state="LOCAL_PREFLIGHT", to_state="LOCAL_QWEN",
            reason="QUOTA_EXHAUSTED", evidence_source="SUPPORTED_QUOTA_TELEMETRY",
        )
    reopened.close()


def test_cloud_recovery_waits_for_safe_boundary_then_routes_review_same_job(tmp_path):
    _root, repository, job, controller = setup(tmp_path)
    controller.failover(
        job.job_id, exhausted(),
        evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
        signal_id="failover",
    )
    before = len(repository.provider_history(job.job_id))
    mutating = controller.recover_cloud_at_safe_boundary(
        job.job_id, available(), signal_id="recovery", safe_boundary=True, mutating_step=True,
    )
    assert mutating["state"] == "CONTINUE_SAME_JOB"
    assert len(repository.provider_history(job.job_id)) == before
    not_safe = controller.recover_cloud_at_safe_boundary(
        job.job_id, available(), signal_id="recovery", safe_boundary=False, mutating_step=False,
    )
    assert not_safe["state"] == "CONTINUE_SAME_JOB"
    recovered = controller.recover_cloud_at_safe_boundary(
        job.job_id, available(), signal_id="recovery", safe_boundary=True, mutating_step=False,
    )
    assert recovered["job_id"] == job.job_id
    assert recovered["current_provider"] == "OPENAI_CODEX"
    assert recovered["state"] == "REVIEW"
    assert [item["to_state"] for item in repository.provider_history(job.job_id)[-2:]] == [
        "SAFE_BOUNDARY", "REVIEW",
    ]
    # Replay is idempotent and does not silently restart or duplicate history.
    again = controller.recover_cloud_at_safe_boundary(
        job.job_id, available(), signal_id="recovery", safe_boundary=True, mutating_step=False,
    )
    assert again == recovered and len(repository.provider_history(job.job_id)) == before + 2
    repository.close()


def test_controller_has_no_approval_git_deploy_or_cloud_model_turn_authority(tmp_path):
    _root, repository, job, controller = setup(tmp_path)
    controller.failover(
        job.job_id, exhausted(),
        evidence_source=AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
        signal_id="bounded-authority",
    )
    assert not any(hasattr(controller, name) for name in (
        "approve", "commit", "push", "merge", "deploy", "run_cloud_model_turn",
    ))
    assert repository.get_job(job.job_id).status is JobStatus.QUEUED
    repository.close()
