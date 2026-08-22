import threading
import time
from pathlib import Path

import pytest

from local_ai_control.services.supervisor import (
    AI_ROOT, CodexTaskSpec, DurableReviewRunner, JobStatus, LeaseKeepingRunner,
    LeaseLostError, ReviewFinding, ReviewResult, ReviewTaskSpec, StageResult,
    StageResultStatus, StaticPassRunner, SupervisorRepository, WorkflowStage,
    WorkflowSupervisor, default_demo_runners, recursive_private_sanitize,
)
import local_ai_control.services.supervisor_round2 as round2


def make_repo(tmp_path):
    repo = SupervisorRepository(tmp_path / "supervisor.db")
    repo.migrate()
    return repo


def review_spec(prompt="safe durable reviewer task"):
    return ReviewTaskSpec(
        AI_ROOT, (AI_ROOT / "control-plane",), prompt, True, "LOW", 60, "REVIEW",
        round2.REVIEW_RESULT_SCHEMA,
    )


def prepare_review(repo, job_id, review_round=0):
    repo.update_job(job_id, current_stage=WorkflowStage.REVIEW, review_round=review_round)


def test_default_review_runner_is_durable_and_real_execution_remains_disabled():
    assert isinstance(default_demo_runners(False)[WorkflowStage.REVIEW], DurableReviewRunner)


def test_reviewer_work_unit_reopen_reconstruct_and_owner_round_binding(tmp_path):
    repo = make_repo(tmp_path)
    repo.create_job("review", "owner", job_id="job-review")
    prepare_review(repo, "job-review")
    unit = repo.create_review_work_unit("job-review", "owner", 1, review_spec(), "rw-1")
    assert unit.read_only and unit.model_role == "REVIEW"
    repo.close()
    repo = make_repo(tmp_path)
    assert repo.review_work_unit_for_round("job-review", "owner", 1).review_work_unit_id == "rw-1"
    assert repo.reconstruct_reviewer_task("job-review", "owner", 1).task_prompt == "safe durable reviewer task"
    with pytest.raises(PermissionError):
        repo.get_review_work_unit("rw-1", "job-review", "other", 1)
    with pytest.raises(PermissionError):
        repo.get_review_work_unit("rw-1", "job-review", "owner", 2)
    repo.create_job("other", "owner", job_id="job-other")
    with pytest.raises(PermissionError):
        repo.get_review_work_unit("rw-1", "job-other", "owner", 1)
    repo.close()


def test_review_task_is_read_only_review_role_and_path_bounded():
    assert review_spec().validate()["read_only"] is True
    with pytest.raises(PermissionError):
        ReviewTaskSpec(AI_ROOT, (AI_ROOT,), "safe", False, "LOW", 60, "REVIEW", {}).validate()
    with pytest.raises(ValueError):
        ReviewTaskSpec(AI_ROOT, (AI_ROOT,), "safe", True, "LOW", 60, "CODE", {}).validate()
    with pytest.raises(PermissionError):
        ReviewTaskSpec(AI_ROOT, (Path("/tmp"),), "safe", True, "LOW", 60, "REVIEW", round2.REVIEW_RESULT_SCHEMA).validate()
    with pytest.raises(ValueError):
        ReviewTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), "safe", True, "LOW", 60, "REVIEW", {"type": "array"}).validate()


def test_review_result_submission_idempotent_conflict_and_schema_rules(tmp_path):
    repo = make_repo(tmp_path)
    repo.create_job("review", "owner", job_id="job-result")
    prepare_review(repo, "job-result")
    unit = repo.create_review_work_unit("job-result", "owner", 1, review_spec(), "rw-result")
    result = ReviewResult("FAIL", (ReviewFinding("HIGH", "control-plane/src/local_ai_control/services/supervisor_contracts.py", "evidence", "fix"),))
    first = repo.submit_review_result("job-result", "owner", 1, unit.review_work_unit_id, result)
    second = repo.submit_review_result("job-result", "owner", 1, unit.review_work_unit_id, result)
    assert first.result_hash == second.result_hash
    with pytest.raises(ValueError):
        repo.submit_review_result("job-result", "owner", 1, unit.review_work_unit_id,
                                  ReviewResult("FAIL", (ReviewFinding("LOW", "control-plane/src/local_ai_control/services/supervisor_round2_review.py", "e2", "f2"),)))
    with pytest.raises(ValueError):
        repo.submit_review_result("job-result", "owner", 1, unit.review_work_unit_id,
                                  ReviewResult("PASS", (ReviewFinding("LOW", "control-plane/src/local_ai_control/services/supervisor_contracts.py", "e", "f"),)))
    with pytest.raises(ValueError):
        repo.submit_review_result("job-result", "owner", 1, unit.review_work_unit_id, ReviewResult("FAIL"))
    repo.close()


def test_submitted_review_result_survives_restart_and_integrity_check(tmp_path):
    repo = make_repo(tmp_path)
    repo.create_job("review", "owner", job_id="job-restart")
    prepare_review(repo, "job-restart")
    unit = repo.create_review_work_unit("job-restart", "owner", 1, review_spec(), "rw-restart")
    repo.submit_review_result("job-restart", "owner", 1, unit.review_work_unit_id, ReviewResult("PASS"))
    repo.close()
    repo = make_repo(tmp_path)
    assert repo.submitted_review_result("job-restart", "owner", 1, "rw-restart").status == "PASS"
    repo.db.execute("UPDATE supervisor_review_results SET result_json='{}' WHERE review_work_unit_id='rw-restart'")
    repo.db.commit()
    with pytest.raises(ValueError):
        repo.submitted_review_result("job-restart", "owner", 1, "rw-restart")
    repo.close()


def test_supervisor_consumes_durable_review_result_after_original_turn_is_gone(tmp_path):
    repo = make_repo(tmp_path)
    job = repo.create_job("review", "owner", job_id="job-consume")
    repo.update_job(job.job_id, current_stage=WorkflowStage.REVIEW)
    runners = {stage: StaticPassRunner(stage.value) for stage in WorkflowStage if stage is not WorkflowStage.DONE}
    runners[WorkflowStage.REVIEW] = DurableReviewRunner()
    supervisor = WorkflowSupervisor(repo, runners)
    assert supervisor.acquire_singleton()
    pending = supervisor.run_job_once(job.job_id)
    assert pending.status is JobStatus.WAITING and pending.resume_state == "REVIEW_RESULT_PENDING"
    unit = repo.review_work_unit_for_round(job.job_id, "owner", 1)
    supervisor.release_singleton(); repo.close()

    submitter = make_repo(tmp_path)
    submitter.submit_review_result(job.job_id, "owner", 1, unit.review_work_unit_id, ReviewResult("PASS"))
    submitter.close()

    repo = make_repo(tmp_path)
    supervisor = WorkflowSupervisor(repo, runners)
    assert supervisor.acquire_singleton()
    consumed = supervisor.run_job_once(job.job_id)
    assert consumed.current_stage is WorkflowStage.SECURITY and consumed.status is JobStatus.QUEUED
    supervisor.release_singleton(); repo.close()


def test_work_unit_immutable_manifest_exact_replay_only(tmp_path):
    repo = make_repo(tmp_path)
    repo.create_job("work", "owner", job_id="job-work")
    base = CodexTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), "same prompt", "LOW", 60, "CODE", {"type": "object"})
    repo.create_work_unit("job-work", "owner", WorkflowStage.PRODUCER, base, "wu-immutable")
    assert repo.create_work_unit("job-work", "owner", WorkflowStage.PRODUCER, base, "wu-immutable").work_unit_id == "wu-immutable"
    variants = (
        CodexTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), "same prompt", "LOW", 61, "CODE", {"type": "object"}),
        CodexTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), "same prompt", "HIGH", 60, "CODE", {"type": "object"}),
        CodexTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), "same prompt", "LOW", 60, "OTHER", {"type": "object"}),
        CodexTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), "same prompt", "LOW", 60, "CODE", {"type": "array"}),
    )
    for variant in variants:
        with pytest.raises(ValueError):
            repo.create_work_unit("job-work", "owner", WorkflowStage.PRODUCER, variant, "wu-immutable")
    with pytest.raises(PermissionError):
        repo.create_work_unit("job-work", "owner", WorkflowStage.PRODUCER,
                              CodexTaskSpec(AI_ROOT, (AI_ROOT,), "same prompt", "LOW", 60, "CODE", {}),
                              "wu-immutable")
    with pytest.raises(PermissionError):
        repo.create_work_unit("job-work", "owner", WorkflowStage.PRODUCER,
                              CodexTaskSpec(Path("/tmp"), (Path("/tmp"),), "same prompt", "LOW", 60, "CODE", {}),
                              "wu-immutable")
    repo.close()


def test_work_unit_spec_hash_is_persisted_and_read_integrity_is_verified(tmp_path):
    repo = make_repo(tmp_path)
    repo.create_job("work", "owner", job_id="job-hash")
    spec = CodexTaskSpec(AI_ROOT, (AI_ROOT / "control-plane",), "prompt", "LOW", 60, "CODE", {"type": "object"})
    repo.create_work_unit("job-hash", "owner", WorkflowStage.PRODUCER, spec, "wu-hash")
    row = repo.db.execute("SELECT spec_hash FROM supervisor_work_units WHERE work_unit_id='wu-hash'").fetchone()
    assert row["spec_hash"] and len(row["spec_hash"]) == 64
    repo.db.execute("UPDATE supervisor_work_units SET timeout_seconds=61 WHERE work_unit_id='wu-hash'")
    repo.db.commit()
    with pytest.raises(ValueError):
        repo.get_work_unit("wu-hash", "job-hash", "owner")
    repo.close()


def test_recursive_sensitive_metadata_never_persists_raw_nested_values(tmp_path):
    raw_prompt = "safe but private project instructions"
    raw_token = "synthetic bearer value"
    repo = make_repo(tmp_path)
    job = repo.create_job("meta", "owner", job_id="job-meta",
                          metadata={"task": {"prompt": raw_prompt}, "items": [{"authorization": raw_token}], "normal": "ok"})
    stored = repo.db.execute("SELECT metadata_json FROM supervisor_jobs WHERE job_id=?", (job.job_id,)).fetchone()[0]
    assert raw_prompt not in stored and raw_token not in stored and '"normal": "ok"' in stored
    repo.record_event(job.job_id, "NESTED", payload={"deep": {"task": [{"prompt": raw_prompt}]}})
    event = repo.db.execute("SELECT payload_json FROM supervisor_events WHERE event_type='NESTED'").fetchone()[0]
    assert raw_prompt not in event and "prompt_sha256" in event
    repo.close()


def test_recursive_private_sanitizer_keeps_normal_metadata_readable():
    value = recursive_private_sanitize({"normal": {"value": "readable"}, "a": [{"token": "private"}]})
    assert value["normal"]["value"] == "readable"
    assert "token" not in value["a"][0] and "token_sha256" in value["a"][0]


def test_lease_keeper_renews_beyond_original_short_ttl_and_blocks_takeover(tmp_path):
    first = make_repo(tmp_path)
    second = SupervisorRepository(first.path); second.migrate()
    assert first.acquire_lock("owner-a", 101, ttl=0.04)
    started = threading.Event()
    class Slow:
        def run(self, _context):
            started.set(); time.sleep(0.14); return StageResult.passed("done")
    result = {}
    def run(): result["value"] = LeaseKeepingRunner(Slow(), first, "owner-a", 0.04, 0.005).run(None)
    thread = threading.Thread(target=run); thread.start(); started.wait(1); time.sleep(0.08)
    assert not second.acquire_lock("owner-b", 202, ttl=1)
    thread.join(1)
    assert result["value"].status is StageResultStatus.PASS
    first.release_lock("owner-a"); first.close(); second.close()


def test_heartbeat_failure_attempts_cancel_and_denies_stage_commit(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    repo.create_job("lease", "owner", job_id="job-lease")
    repo.set_active_lease("owner-a")
    repo.lease_failed = False
    class Slow:
        def __init__(self): self.cancelled = False
        def run(self, _context): time.sleep(0.04); return StageResult.passed("pass")
        def cancel(self): self.cancelled = True
    slow = Slow()
    monkeypatch.setattr(repo, "heartbeat_external", lambda *args, **kwargs: False)
    with pytest.raises(LeaseLostError):
        LeaseKeepingRunner(slow, repo, "owner-a", 1, 0.005).run(None)
    assert slow.cancelled and repo.lease_failed
    with pytest.raises(LeaseLostError):
        repo.update_job("job-lease", status=JobStatus.COMPLETED)
    repo.set_active_lease(None); repo.close()


def test_old_lease_owner_cannot_transition_after_takeover(tmp_path):
    first = make_repo(tmp_path); second = SupervisorRepository(first.path); second.migrate()
    first.create_job("lease", "owner", job_id="job-takeover")
    assert first.acquire_lock("a", 1, ttl=0.01); first.set_active_lease("a")
    time.sleep(0.02); assert second.acquire_lock("b", 2, ttl=1)
    with pytest.raises(LeaseLostError):
        first.update_job("job-takeover", status=JobStatus.COMPLETED)
    first.set_active_lease(None); second.release_lock("b"); first.close(); second.close()


def test_old_lease_owner_cannot_finish_stage_after_takeover(tmp_path):
    first = make_repo(tmp_path); second = SupervisorRepository(first.path); second.migrate()
    job = first.create_job("lease", "owner", job_id="job-finish")
    assert first.acquire_lock("a", 1, ttl=0.01); first.set_active_lease("a")
    started = first.begin_stage(job)
    assert started
    time.sleep(0.02); assert second.acquire_lock("b", 2, ttl=1)
    with pytest.raises(LeaseLostError):
        first.finish_stage(started[0], job.job_id, WorkflowStage.INTAKE, StageResult.passed("stale pass"))
    run = first.db.execute("SELECT status FROM supervisor_stage_runs WHERE run_id=?", (started[0],)).fetchone()
    assert run["status"] == "RUNNING"
    first.set_active_lease(None); second.release_lock("b"); first.close(); second.close()
