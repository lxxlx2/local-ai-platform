import hashlib
import os
import stat
from pathlib import Path

import pytest

from local_ai_control.services.supervisor import (
    AI_ROOT, CodexTaskSpec, JobStatus, LeaseLostError, MockCodexRunner, MockReviewRunner,
    ReviewFinding, ReviewResult, ReviewTaskSpec, SecurityRunner, StageContext, StageResultStatus, StaticPassRunner,
    SupervisorRepository, WorkflowStage, WorkflowSupervisor, default_demo_runners,
    ensure_private_directory,
)
import local_ai_control.services.supervisor_round2 as round2
from local_ai_control.supervisor import process_identity


def repo(tmp_path):
    value = SupervisorRepository(tmp_path / "runtime" / "supervisor.db")
    value.migrate()
    return value


def demo_runners():
    runners = default_demo_runners(real_validation=False)
    runners[WorkflowStage.SECURITY] = StaticPassRunner("security")
    runners[WorkflowStage.GIT_GATE] = StaticPassRunner("git")
    return runners


def test_durable_work_unit_survives_reopen_and_is_owner_job_path_secret_safe(tmp_path):
    first = repo(tmp_path)
    job = first.create_job("real-like", "owner-a")
    prompt = "Implement a bounded local workflow repair without credentials."
    spec = CodexTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), prompt, "LOW", 60, "CODE", {"type": "object"})
    unit = first.create_work_unit(job.job_id, "owner-a", WorkflowStage.PRODUCER, spec, work_unit_id="wu-1")
    assert unit.prompt_sha256 == hashlib.sha256(prompt.encode()).hexdigest()
    first.close()

    second = SupervisorRepository(tmp_path / "runtime" / "supervisor.db"); second.migrate()
    restored = second.get_work_unit("wu-1", job.job_id, "owner-a")
    assert restored == unit
    assert second.load_work_unit_prompt("wu-1", job.job_id, "owner-a") == prompt
    assert second.reconstruct_codex_task(job.job_id, "owner-a", WorkflowStage.PRODUCER).task_prompt == prompt
    with pytest.raises(PermissionError):
        second.get_work_unit("wu-1", job.job_id, "owner-b")
    other = second.create_job("other", "owner-a", mutation_capable=False)
    with pytest.raises(PermissionError):
        second.get_work_unit("wu-1", other.job_id, "owner-a")
    with pytest.raises(ValueError):
        second.create_work_unit(
            job.job_id, "owner-a", WorkflowStage.REVISION,
            CodexTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), "pass" + "word=synthetic-sensitive-value", "LOW", 60, "CODE", {}),
        )
    with pytest.raises(PermissionError):
        CodexTaskSpec(AI_ROOT, (Path("/tmp"),), "safe prompt", "LOW", 60, "CODE", {}).validate()
    prompt_path = second.content_store.root / restored.prompt_content_ref
    assert stat.S_IMODE(second.content_store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(prompt_path.stat().st_mode) == 0o600
    second.close()


def test_review_findings_persist_reopen_redact_round_isolate_and_revision_reads(tmp_path):
    first = repo(tmp_path)
    job = first.create_job("review", "owner")
    first.update_job(job.job_id, current_stage=WorkflowStage.REVIEW)
    spec = ReviewTaskSpec(AI_ROOT, (AI_ROOT / "control-plane", AI_ROOT / "docs"), "review", True, "LOW", 60,
                          "REVIEW", round2.REVIEW_RESULT_SCHEMA)
    first.create_review_work_unit(job.job_id, "owner", 1, spec, "round1-unit")
    findings = (
        ReviewFinding("BLOCKING", "control-plane/src/local_ai_control/services/supervisor_contracts.py", "plain evidence", "plain fix"),
        ReviewFinding("HIGH", "control-plane/src/local_ai_control/services/supervisor_round2_review.py", "pass" + "word=synthetic-sensitive-value", "safe fix"),
    )
    saved = first.persist_review_findings(job.job_id, "owner", 1, findings)
    assert len(saved) == 2
    assert saved[1].evidence == "[REDACTED_BY_SECRET_FIREWALL]"
    first.close()

    second = SupervisorRepository(tmp_path / "runtime" / "supervisor.db"); second.migrate()
    round1 = second.review_findings(job.job_id, "owner", 1)
    assert len(round1) == 2 and round1[0].evidence == "plain evidence"
    second.update_job(job.job_id, review_round=1, current_stage=WorkflowStage.REVIEW)
    second.create_review_work_unit(job.job_id, "owner", 2, spec, "round2-unit")
    second.persist_review_findings(job.job_id, "owner", 2, (
        ReviewFinding("MEDIUM", "control-plane/src/local_ai_control/services/supervisor_workflow.py", "round two", "round two fix"),
    ))
    assert len(second.review_findings(job.job_id, "owner", 1)) == 2
    assert len(second.review_findings(job.job_id, "owner", 2)) == 1
    with pytest.raises(PermissionError):
        second.review_findings(job.job_id, "other-owner", 1)
    with pytest.raises(PermissionError):
        second.persist_review_findings(job.job_id, "owner", 1, (
            ReviewFinding("HIGH", "../escape", "x", "y"),
        ))
    with pytest.raises(ValueError):
        ReviewResult("PASS", (ReviewFinding("LOW", "control-plane/src/local_ai_control/services/supervisor_contracts.py", "x", "y"),)).to_stage_result()
    second.update_job(job.job_id, review_round=1, current_stage=WorkflowStage.REVISION)
    current = second.get_job(job.job_id)
    context = StageContext(current, WorkflowStage.REVISION, 1, "revision-key", 30, second)
    assert len(context.current_review_findings()) == 2
    with pytest.raises(ValueError):
        second.persist_review_findings(job.job_id, "owner", 1, tuple(
            ReviewFinding("LOW", f"control-plane/f{i}.py", "e", "f") for i in range(101)
        ))
    second.close()


def test_review_fail_persists_actionable_findings_and_revision_consumes_history(tmp_path):
    repository = repo(tmp_path)
    supervisor = WorkflowSupervisor(repository, demo_runners())
    assert supervisor.acquire_singleton()
    job = supervisor.create_demo("owner")
    result = supervisor.run_until_terminal(job.job_id)
    assert result.status is JobStatus.COMPLETED
    findings = repository.review_findings(job.job_id, "owner", 1)
    assert len(findings) == 1
    assert findings[0].status == "CONSUMED" and findings[0].consumed_by_revision
    supervisor.release_singleton(); repository.close()


def test_blocked_jobs_cannot_pause_resume_or_retry_around_gates(tmp_path):
    repository = repo(tmp_path)
    supervisor = WorkflowSupervisor(repository, demo_runners())
    job = repository.create_job("mutating", "owner")
    repository.update_job(job.job_id, current_stage=WorkflowStage.PRODUCER)
    assert repository.begin_stage(repository.get_job(job.job_id))
    assert supervisor.recover_interrupted() == 1
    blocked = supervisor.status(job.job_id)
    assert blocked.status is JobStatus.BLOCKED and blocked.resume_state == "BLOCKED_REQUIRES_RECONCILIATION"
    assert supervisor.pause(job.job_id).resume_state == "BLOCKED_REQUIRES_RECONCILIATION"
    assert supervisor.resume(job.job_id).status is JobStatus.BLOCKED
    assert supervisor.retry(job.job_id).status is JobStatus.BLOCKED
    for reason in ("MAX_REVIEW_ROUNDS", "SECURITY_BLOCKED", "GIT_GATE_BLOCKED"):
        repository.update_job(job.job_id, status=JobStatus.BLOCKED, resume_state=reason)
        assert supervisor.pause(job.job_id).resume_state == reason
        assert supervisor.resume(job.job_id).resume_state == reason
        assert supervisor.retry(job.job_id).resume_state == reason
    ordinary = repository.create_job("ordinary", "owner", mutation_capable=False)
    assert supervisor.pause(ordinary.job_id).resume_state == "PAUSED"
    resumed = supervisor.resume(ordinary.job_id)
    assert resumed.status is JobStatus.QUEUED and resumed.resume_state is None
    repository.close()


def test_lease_loss_is_fail_closed_and_new_owner_can_consume(tmp_path):
    a_repo = repo(tmp_path)
    b_repo = SupervisorRepository(a_repo.path); b_repo.migrate()
    a = WorkflowSupervisor(a_repo, demo_runners())
    b = WorkflowSupervisor(b_repo, demo_runners())
    assert a.acquire_singleton(pid=1001)
    job = a.create_demo("owner")
    a_repo.db.execute("UPDATE supervisor_locks SET expires_at=0")
    a_repo.db.commit()
    assert b.acquire_singleton(pid=1002)
    with pytest.raises(LeaseLostError):
        a.run_once()
    assert not a.locked
    assert a_repo.latest_stage_runs(job.job_id) == []
    progressed = b.run_once()
    assert progressed.job_id == job.job_id
    assert b_repo.counts()["active"] <= 1
    b.release_singleton(); a_repo.close(); b_repo.close()


def test_heartbeat_transient_failure_is_fail_closed(tmp_path, monkeypatch):
    repository = repo(tmp_path)
    supervisor = WorkflowSupervisor(repository, demo_runners())
    assert supervisor.acquire_singleton()
    supervisor.create_demo("owner")
    monkeypatch.setattr(repository, "heartbeat_lock", lambda *args, **kwargs: False)
    with pytest.raises(LeaseLostError):
        supervisor.run_once()
    assert not supervisor.locked
    assert repository.db.execute("SELECT COUNT(*) FROM supervisor_stage_runs").fetchone()[0] == 0
    repository.close()


def test_security_candidate_scan_is_fail_closed_for_oversized_binary_symlink_and_secret(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    runner = SecurityRunner(root)
    large_clean = root / "large.txt"; large_clean.write_bytes(b"a" * 1_000_001)
    result = runner._scan_candidates(["large.txt"])
    assert result.status is StageResultStatus.FAIL and result.error == "OVERSIZED_UNSCANNED_CANDIDATE"
    assert result.metrics["oversized"] == 1 and result.metrics["files_scanned"] == 0
    large_secret = root / "large-secret.txt"
    large_secret.write_bytes(b"a" * 1_000_001 + b" pass" + b"word=synthetic-sensitive-value")
    assert runner._scan_candidates(["large-secret.txt"]).status is StageResultStatus.FAIL
    binary = root / "candidate.bin"; binary.write_bytes(b"abc\x00def")
    result = runner._scan_candidates(["candidate.bin"])
    assert result.error == "BINARY_CANDIDATE_UNAPPROVED" and result.metrics["binary"] == 1
    secret = root / "secret.txt"; secret.write_text("pass" + "word=synthetic-sensitive-value")
    assert runner._scan_candidates(["secret.txt"]).error == "SECRET_SCAN"
    outside = tmp_path / "outside.txt"; outside.write_text("clean")
    (root / "link.txt").symlink_to(outside)
    assert runner._scan_candidates(["link.txt"]).error == "UNSCANNABLE_CANDIDATE"


def test_runtime_permissions_and_chmod_failure_are_explicit(tmp_path, monkeypatch):
    repository = repo(tmp_path)
    assert stat.S_IMODE(repository.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(repository.path.stat().st_mode) == 0o600
    repository.close()
    target = tmp_path / "cannot-chmod"
    original = Path.chmod
    def fail_chmod(self, mode):
        if self == target:
            raise OSError("denied")
        return original(self, mode)
    monkeypatch.setattr(Path, "chmod", fail_chmod)
    with pytest.raises(PermissionError):
        ensure_private_directory(target)


def test_run_until_terminal_targets_only_requested_job(tmp_path):
    repository = repo(tmp_path)
    supervisor = WorkflowSupervisor(repository, demo_runners())
    assert supervisor.acquire_singleton()
    a = supervisor.create_demo("owner", job_id="job-a")
    with pytest.raises(RuntimeError, match="MAX_MUTATING_JOBS_IN_SYSTEM"):
        supervisor.create_demo("owner", job_id="job-b")
    result = supervisor.run_until_terminal(a.job_id)
    assert result.status is JobStatus.COMPLETED
    supervisor.release_singleton(); repository.close()


def test_exact_process_identity_uses_argv_executable_and_start_identity(tmp_path, monkeypatch):
    exact = process_identity.ProcessIdentity(
        123, str(process_identity.CONTROL_PLANE_PYTHON), process_identity.EXPECTED_ARGV, "Fri Aug 21 20:00:00 2026",
    )
    path = tmp_path / "identity.json"
    process_identity.write_identity(path, exact)
    monkeypatch.setattr(process_identity, "process_snapshot", lambda pid: exact)
    assert process_identity.identity_status(path) == ("MATCH", 123)
    substring_only = process_identity.ProcessIdentity(
        123, "/usr/bin/python", ("unrelated", "local_ai_control.supervisor.app", "daemon"), exact.start_identity,
    )
    monkeypatch.setattr(process_identity, "process_snapshot", lambda pid: substring_only)
    assert process_identity.identity_status(path) == ("MISMATCH", 123)
    reused = process_identity.ProcessIdentity(123, exact.executable, exact.argv, "Fri Aug 21 21:00:00 2026")
    monkeypatch.setattr(process_identity, "process_snapshot", lambda pid: reused)
    assert process_identity.identity_status(path) == ("MISMATCH", 123)
    monkeypatch.setattr(process_identity, "process_snapshot", lambda pid: None)
    assert process_identity.identity_status(path) == ("DEAD", 123)
    assert process_identity.identity_status(tmp_path / "missing") == ("MISSING", None)


def test_scripts_never_use_substring_or_broad_kill():
    script_root = Path("/Users/jerson/AI/control-plane/scripts")
    for name in ("start-supervisor.sh", "stop-supervisor.sh", "status-supervisor.sh"):
        source = (script_root / name).read_text()
        assert "process_identity" in source
        assert '== *"$EXPECTED"*' not in source
        assert "pkill" not in source and "killall" not in source and "kill -9" not in source
