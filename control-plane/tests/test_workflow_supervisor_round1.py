from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from local_ai_control.services import supervisor_payloads, supervisor_runners
from local_ai_control.services.supervisor import (
    AI_ROOT,
    CodexTaskSpec,
    JobStatus,
    LeaseLostError,
    MockCodexRunner,
    MockReviewRunner,
    ReviewFinding,
    ReviewResult,
    StageContext,
    StageResultStatus,
    StaticPassRunner,
    SupervisorRepository,
    WorkflowStage,
    WorkflowSupervisor,
    ensure_private_directory,
)
from local_ai_control.supervisor.process_identity import ProcessIdentity, identities_match


def repo(tmp_path):
    result = SupervisorRepository(tmp_path / "supervisor.db")
    result.migrate()
    return result


def demo_runners():
    return {
        WorkflowStage.INTAKE: StaticPassRunner("intake"),
        WorkflowStage.PRODUCER: MockCodexRunner(),
        WorkflowStage.VALIDATION: StaticPassRunner("validation"),
        WorkflowStage.SELF_ACCEPTANCE: StaticPassRunner("self"),
        WorkflowStage.REVIEW: MockReviewRunner(),
        WorkflowStage.REVISION: MockCodexRunner(),
        WorkflowStage.SECURITY: StaticPassRunner("security"),
        WorkflowStage.GIT_GATE: StaticPassRunner("git"),
    }


def safe_spec(prompt="safe durable task"):
    return CodexTaskSpec(
        AI_ROOT,
        (AI_ROOT / "control-plane",),
        prompt,
        "LOW",
        60,
        "CODE",
        {"type": "object"},
    )


def test_work_unit_survives_reopen_with_prompt_hash_and_scope(tmp_path):
    first = repo(tmp_path)
    job = first.create_job("durable", "owner")
    unit = first.create_work_unit(job.job_id, "owner", WorkflowStage.PRODUCER, safe_spec(), "wu-1")
    prompt = first.load_work_unit_prompt(unit.work_unit_id, job.job_id, "owner")
    assert prompt == "safe durable task"
    first.close()

    second = SupervisorRepository(tmp_path / "supervisor.db")
    second.migrate()
    restored = second.get_work_unit("wu-1", job.job_id, "owner")
    reconstructed = second.reconstruct_codex_task(job.job_id, "owner", WorkflowStage.PRODUCER)
    assert restored.prompt_sha256 == unit.prompt_sha256
    assert reconstructed.task_prompt == prompt
    assert reconstructed.validate()["task_prompt_sha256"] == unit.prompt_sha256
    with pytest.raises(PermissionError):
        second.get_work_unit("wu-1", job.job_id, "other-owner")
    second.close()


def test_secret_prompt_is_rejected_before_content_persistence(tmp_path):
    repository = repo(tmp_path)
    job = repository.create_job("secret", "owner")
    unsafe = "pass" + "word=example-sensitive-value"
    with pytest.raises(ValueError):
        repository.create_work_unit(job.job_id, "owner", WorkflowStage.PRODUCER, safe_spec(unsafe), "wu-secret")
    assert not list((tmp_path / "content").glob("*.prompt"))
    repository.close()


def test_work_unit_paths_remain_restricted(tmp_path):
    repository = repo(tmp_path)
    job = repository.create_job("scope", "owner")
    with pytest.raises(PermissionError):
        repository.create_work_unit(
            job.job_id,
            "owner",
            WorkflowStage.PRODUCER,
            CodexTaskSpec(AI_ROOT, (Path("/tmp"),), "safe", "LOW", 60, "CODE", {}),
        )
    repository.close()


def test_review_findings_survive_reopen_and_revision_consumes_same_round(tmp_path):
    first = repo(tmp_path)
    supervisor = WorkflowSupervisor(first, demo_runners())
    assert supervisor.acquire_singleton()
    job = supervisor.create_demo("owner")
    for _ in range(5):
        supervisor.run_job_once(job.job_id)
    revision = supervisor.status(job.job_id)
    assert revision.current_stage is WorkflowStage.REVISION and revision.review_round == 1
    stored = first.review_findings(job.job_id, "owner", 1)
    assert len(stored) == 1 and stored[0].recommended_fix == "synthetic demo revision"
    supervisor.release_singleton(); first.close()

    second = SupervisorRepository(tmp_path / "supervisor.db"); second.migrate()
    restored = second.review_findings(job.job_id, "owner", 1)
    context = StageContext(second.get_job(job.job_id), WorkflowStage.REVISION, 1, "revision-test", 30, second)
    result = MockCodexRunner().run(context)
    assert result.status is StageResultStatus.PASS and result.metrics["findings_consumed"] == 1
    assert restored[0].integrity_hash == stored[0].integrity_hash
    second.close()


def test_secret_review_evidence_is_redacted_before_db(tmp_path):
    repository = repo(tmp_path)
    job = repository.create_job("finding", "owner")
    sensitive = "pass" + "word=example-sensitive-value"
    repository.persist_review_findings(
        job.job_id,
        "owner",
        1,
        [ReviewFinding("HIGH", "control-plane/tests/test_control.py", sensitive, sensitive)],
    )
    row = repository.db.execute("SELECT evidence_summary,recommended_fix FROM supervisor_review_findings").fetchone()
    assert sensitive not in row[0] and sensitive not in row[1]
    assert "REDACTED_BY_SECRET_FIREWALL" in row[0]
    repository.close()


def test_review_path_round_owner_and_pass_contract_are_isolated(tmp_path):
    repository = repo(tmp_path)
    first = repository.create_job("first", "owner-1")
    second = repository.create_job("second", "owner-2")
    with pytest.raises(PermissionError):
        repository.persist_review_findings(
            first.job_id, "owner-1", 1, [ReviewFinding("HIGH", "../escape", "x", "fix")]
        )
    repository.persist_review_findings(
        first.job_id, "owner-1", 1,
        [ReviewFinding("HIGH", "control-plane/a.py", "round one", "fix one")],
    )
    repository.persist_review_findings(
        first.job_id, "owner-1", 2,
        [ReviewFinding("MEDIUM", "control-plane/b.py", "round two", "fix two")],
    )
    assert repository.review_findings(first.job_id, "owner-1", 1)[0].evidence == "round one"
    assert repository.review_findings(first.job_id, "owner-1", 2)[0].evidence == "round two"
    with pytest.raises(PermissionError):
        repository.review_findings(first.job_id, "owner-2", 1)
    assert repository.review_findings(second.job_id, "owner-2", 1) == []
    with pytest.raises(ValueError):
        ReviewResult("PASS", (ReviewFinding("LOW", "control-plane/a.py", "x", "y"),)).to_stage_result()
    repository.close()


def test_finding_history_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor_payloads, "MAX_FINDINGS_PER_JOB", 1)
    repository = repo(tmp_path)
    job = repository.create_job("bound", "owner")
    repository.persist_review_findings(
        job.job_id, "owner", 1, [ReviewFinding("LOW", "control-plane/a.py", "a", "fix")]
    )
    with pytest.raises(ValueError):
        repository.persist_review_findings(
            job.job_id, "owner", 2, [ReviewFinding("LOW", "control-plane/b.py", "b", "fix")]
        )
    repository.close()


def test_blocked_reconciliation_cannot_be_bypassed_by_pause_resume_retry(tmp_path):
    repository = repo(tmp_path)
    supervisor = WorkflowSupervisor(repository, demo_runners())
    job = supervisor.create_demo("owner")
    for reason in ("BLOCKED_REQUIRES_RECONCILIATION", "MAX_REVIEW_ROUNDS", "SECURITY_BLOCK"):
        repository.update_job(job.job_id, status=JobStatus.BLOCKED, resume_state=reason)
        assert supervisor.pause(job.job_id).status is JobStatus.BLOCKED
        assert supervisor.resume(job.job_id).status is JobStatus.BLOCKED
        assert supervisor.retry(job.job_id).status is JobStatus.BLOCKED
        assert supervisor.status(job.job_id).resume_state == reason
    repository.close()


def test_ordinary_pause_resume_still_works(tmp_path):
    repository = repo(tmp_path)
    supervisor = WorkflowSupervisor(repository, demo_runners())
    job = supervisor.create_demo("owner")
    paused = supervisor.pause(job.job_id)
    assert paused.status is JobStatus.WAITING and paused.resume_state == "PAUSED"
    resumed = supervisor.resume(job.job_id)
    assert resumed.status is JobStatus.QUEUED and resumed.resume_state is None
    repository.close()


def test_lost_lease_fails_closed_before_stage_and_new_owner_can_consume(tmp_path):
    first_repo = repo(tmp_path)
    second_repo = SupervisorRepository(first_repo.path); second_repo.migrate()
    first = WorkflowSupervisor(first_repo, demo_runners())
    second = WorkflowSupervisor(second_repo, demo_runners())
    job = first.create_demo("owner")
    assert first.acquire_singleton(pid=1001)
    first_repo.db.execute("UPDATE supervisor_locks SET expires_at=0"); first_repo.db.commit()
    assert second.acquire_singleton(pid=1002)
    with pytest.raises(LeaseLostError):
        first.run_once()
    assert first.locked is False
    assert first_repo.stage_attempts(job.job_id, WorkflowStage.INTAKE) == 0
    advanced = second.run_once()
    assert advanced.current_stage is WorkflowStage.PRODUCER
    second.release_singleton(); first_repo.close(); second_repo.close()


def test_targeted_run_until_terminal_does_not_consume_other_job(tmp_path):
    repository = repo(tmp_path)
    supervisor = WorkflowSupervisor(repository, demo_runners())
    assert supervisor.acquire_singleton()
    first = supervisor.create_demo("owner", "job-a")
    second = supervisor.create_demo("owner", "job-b")
    result = supervisor.run_until_terminal(first.job_id)
    assert result.status is JobStatus.COMPLETED
    untouched = supervisor.status(second.job_id)
    assert untouched.status is JobStatus.QUEUED and untouched.current_stage is WorkflowStage.INTAKE
    assert repository.latest_stage_runs(second.job_id) == []
    supervisor.release_singleton(); repository.close()


def _fake_git_run_factory(candidate_name):
    class Completed:
        def __init__(self, stdout="", returncode=0):
            self.stdout = stdout; self.stderr = ""; self.returncode = returncode
    def fake(argv, **kwargs):
        if argv[:2] == ["git", "ls-files"] and "--others" not in argv:
            return Completed("")
        if argv[:2] == ["git", "diff"]:
            return Completed(candidate_name + "\n")
        if argv[:2] == ["git", "ls-files"] and "--others" in argv:
            return Completed("")
        raise AssertionError(argv)
    return fake


def test_oversized_candidate_fails_closed_without_reading_whole_file(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor_runners, "AI_ROOT", tmp_path.resolve())
    candidate = tmp_path / "large.txt"
    candidate.write_bytes(b"x" * (1_000_001))
    monkeypatch.setattr(supervisor_runners.subprocess, "run", _fake_git_run_factory("large.txt"))
    context = type("C", (), {"timeout_seconds": 30})()
    result = supervisor_runners.SecurityRunner(tmp_path).run(context)
    assert result.status is StageResultStatus.FAIL
    assert result.error == "OVERSIZED_UNSCANNED_CANDIDATE"
    assert result.metrics["oversized"] == 1 and result.metrics["files_scanned"] == 0


def test_binary_candidate_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor_runners, "AI_ROOT", tmp_path.resolve())
    (tmp_path / "candidate.bin").write_bytes(b"safe\x00binary")
    monkeypatch.setattr(supervisor_runners.subprocess, "run", _fake_git_run_factory("candidate.bin"))
    context = type("C", (), {"timeout_seconds": 30})()
    result = supervisor_runners.SecurityRunner(tmp_path).run(context)
    assert result.status is StageResultStatus.FAIL
    assert result.error == "BINARY_UNSCANNED_CANDIDATE"
    assert result.metrics["binary"] == 1


def test_owner_private_runtime_permissions_and_chmod_failure(tmp_path, monkeypatch):
    runtime = ensure_private_directory(tmp_path / "runtime")
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    repository = SupervisorRepository(runtime / "supervisor.db"); repository.migrate()
    assert stat.S_IMODE((runtime / "supervisor.db").stat().st_mode) == 0o600
    repository.close()
    original = Path.chmod
    monkeypatch.setattr(Path, "chmod", lambda self, mode: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(PermissionError):
        ensure_private_directory(tmp_path / "denied")
    monkeypatch.setattr(Path, "chmod", original)


def test_exact_process_identity_rejects_substring_and_reused_pid():
    expected = ProcessIdentity(
        123,
        "/Users/jerson/AI/runtime/control-plane-venv/bin/python",
        (
            "/Users/jerson/AI/runtime/control-plane-venv/bin/python",
            "-m",
            "local_ai_control.supervisor.app",
            "daemon",
        ),
        "Fri Aug 21 20:00:00 2026",
    )
    assert identities_match(expected, expected)
    substring = ProcessIdentity(
        123, "/usr/bin/python3",
        ("/usr/bin/python3", "unrelated-local_ai_control.supervisor.app daemon"),
        expected.start_identity,
    )
    assert not identities_match(expected, substring)
    reused = ProcessIdentity(expected.pid, expected.executable, expected.argv, "Fri Aug 21 21:00:00 2026")
    assert not identities_match(expected, reused)


def test_runtime_payload_path_is_git_ignored():
    gitignore = (AI_ROOT / ".gitignore").read_text()
    assert "runtime/" in gitignore
