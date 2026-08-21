import json
import os
import subprocess
from pathlib import Path

import pytest

from local_ai_control.services.supervisor import (
    AI_ROOT,
    CONTROL_PLANE_PYTHON,
    CONTROL_PLANE_ROOT,
    CodexCapabilityProbe,
    CodexTaskSpec,
    GitGateRunner,
    JobStatus,
    LocalValidationRunner,
    MockCodexRunner,
    MockReviewRunner,
    RealCodexRunner,
    ReviewFinding,
    ReviewResult,
    SafeCommandPolicy,
    SecurityRunner,
    StageResult,
    StageResultStatus,
    StaticPassRunner,
    SupervisorRepository,
    WorkflowStage,
    WorkflowSupervisor,
)


def make_repository(tmp_path):
    repository = SupervisorRepository(tmp_path / "supervisor.db")
    repository.migrate()
    return repository


def deterministic_runners(review=None):
    return {
        WorkflowStage.INTAKE: StaticPassRunner("intake"),
        WorkflowStage.PRODUCER: MockCodexRunner(),
        WorkflowStage.VALIDATION: StaticPassRunner("validation"),
        WorkflowStage.SELF_ACCEPTANCE: StaticPassRunner("self"),
        WorkflowStage.REVIEW: review or MockReviewRunner(),
        WorkflowStage.REVISION: MockCodexRunner(),
        WorkflowStage.SECURITY: StaticPassRunner("security"),
        WorkflowStage.GIT_GATE: StaticPassRunner("git gate"),
    }


def test_schema_contains_required_private_tables_and_job_fields(tmp_path):
    repository = make_repository(tmp_path)
    tables = {row[0] for row in repository.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "supervisor_jobs", "supervisor_stage_runs", "supervisor_events",
        "supervisor_artifacts", "supervisor_locks",
    } <= tables
    job = repository.create_job("demo", "owner-1", metadata={"task_prompt": "not persisted verbatim"})
    assert job.status is JobStatus.QUEUED and job.current_stage is WorkflowStage.INTAKE
    assert "task_prompt" not in job.metadata and "task_prompt_sha256" in job.metadata
    repository.close()


def test_demo_automatically_revises_after_review_fail_and_completes(tmp_path):
    repository = make_repository(tmp_path)
    supervisor = WorkflowSupervisor(repository, deterministic_runners())
    assert supervisor.acquire_singleton()
    job = supervisor.create_demo("owner-1")
    result = supervisor.run_until_terminal(job.job_id)
    assert result.status is JobStatus.COMPLETED
    assert result.current_stage is WorkflowStage.DONE
    assert result.review_round == 1
    stages = [row["stage"] for row in repository.latest_stage_runs(job.job_id)]
    assert stages == [
        "INTAKE", "PRODUCER", "VALIDATION", "SELF_ACCEPTANCE", "REVIEW",
        "REVISION", "VALIDATION", "SELF_ACCEPTANCE", "REVIEW", "SECURITY", "GIT_GATE",
    ]
    events = [event["event_type"] for event in repository.events(job.job_id)]
    assert "REVIEW_FINDINGS_RECEIVED" in events and events[-1] == "JOB_COMPLETED"
    supervisor.release_singleton(); repository.close()


def test_job_creation_and_events_are_idempotent(tmp_path):
    repository = make_repository(tmp_path)
    first = repository.create_job("SUPERVISOR_DEMO", "owner", job_id="double-click")
    second = repository.create_job("SUPERVISOR_DEMO", "owner", job_id="double-click")
    assert first.job_id == second.job_id
    assert repository.record_event(first.job_id, "JOB_RESUMED", dedupe_key="same-event")
    assert not repository.record_event(first.job_id, "JOB_RESUMED", dedupe_key="same-event")
    assert len([event for event in repository.events(first.job_id) if event["dedupe_key"] == "same-event"]) == 1
    with pytest.raises(ValueError):
        repository.create_job("different", "owner", job_id="double-click")
    with pytest.raises(ValueError):
        repository.create_job("unsafe id", "owner", job_id="x" * 47)
    repository.close()


def test_duplicate_resume_does_not_repeat_completed_stage(tmp_path):
    repository = make_repository(tmp_path)
    supervisor = WorkflowSupervisor(repository, deterministic_runners())
    assert supervisor.acquire_singleton()
    job = supervisor.create_demo("owner")
    after_intake = supervisor.run_once()
    assert after_intake.current_stage is WorkflowStage.PRODUCER
    assert supervisor.resume(job.job_id).current_stage is WorkflowStage.PRODUCER
    assert supervisor.resume(job.job_id).current_stage is WorkflowStage.PRODUCER
    supervisor.run_once()
    stages = [row["stage"] for row in repository.latest_stage_runs(job.job_id)]
    assert stages == ["INTAKE", "PRODUCER"]
    supervisor.release_singleton(); repository.close()


def test_pause_resume_cancel_and_owner_isolation_are_idempotent(tmp_path):
    repository = make_repository(tmp_path)
    supervisor = WorkflowSupervisor(repository, deterministic_runners())
    job = supervisor.create_demo("owner-1")
    paused = supervisor.pause(job.job_id, "owner-1")
    assert paused.status is JobStatus.WAITING and paused.resume_state == "PAUSED"
    assert supervisor.pause(job.job_id, "owner-1").resume_state == "PAUSED"
    resumed = supervisor.resume(job.job_id, "owner-1")
    assert resumed.status is JobStatus.QUEUED and resumed.resume_state is None
    canceled = supervisor.cancel(job.job_id, "owner-1")
    assert canceled.status is JobStatus.CANCELED
    assert supervisor.cancel(job.job_id, "owner-1").status is JobStatus.CANCELED
    with pytest.raises(PermissionError):
        supervisor.status(job.job_id, "public-user")
    repository.close()


def test_single_instance_lock_rejects_second_consumer_and_allows_stale_takeover(tmp_path):
    first_repo = make_repository(tmp_path)
    second_repo = SupervisorRepository(first_repo.path); second_repo.migrate()
    first = WorkflowSupervisor(first_repo, deterministic_runners())
    second = WorkflowSupervisor(second_repo, deterministic_runners())
    assert first.acquire_singleton(pid=1001)
    assert not second.acquire_singleton(pid=1002)
    assert first_repo.lock_snapshot()["expires_at"] - first_repo.lock_snapshot()["heartbeat_at"] >= 180
    first_repo.db.execute("UPDATE supervisor_locks SET expires_at=0")
    first_repo.db.commit()
    assert second.acquire_singleton(pid=1002)
    second.release_singleton(); first_repo.close(); second_repo.close()


def test_interrupted_safe_stage_recovers_without_assuming_success(tmp_path):
    first_repo = make_repository(tmp_path)
    first = WorkflowSupervisor(first_repo, deterministic_runners())
    assert first.acquire_singleton(pid=2001)
    job = first.create_demo("owner")
    started = first_repo.begin_stage(job)
    assert started and first_repo.get_job(job.job_id).status is JobStatus.RUNNING
    first_repo.db.execute("UPDATE supervisor_locks SET expires_at=0")
    first_repo.db.commit(); first_repo.close()

    second_repo = SupervisorRepository(tmp_path / "supervisor.db"); second_repo.migrate()
    second = WorkflowSupervisor(second_repo, deterministic_runners())
    assert second.acquire_singleton(pid=2002)
    assert second.recover_interrupted() == 1
    recovered = second.status(job.job_id)
    assert recovered.status is JobStatus.QUEUED
    assert recovered.resume_state == "INTERRUPTED_SAFE_RETRY"
    assert second_repo.latest_stage_runs(job.job_id)[0]["status"] == "INTERRUPTED"
    completed = second.run_until_terminal(job.job_id)
    assert completed.status is JobStatus.COMPLETED
    second.release_singleton(); second_repo.close()


def test_interrupted_mutating_stage_requires_reconciliation(tmp_path):
    repository = make_repository(tmp_path)
    job = repository.create_job("producer recovery", "owner")
    repository.update_job(job.job_id, current_stage=WorkflowStage.PRODUCER)
    job = repository.get_job(job.job_id)
    assert repository.begin_stage(job)
    supervisor = WorkflowSupervisor(repository, deterministic_runners())
    assert supervisor.recover_interrupted() == 1
    recovered = supervisor.status(job.job_id)
    assert recovered.status is JobStatus.BLOCKED
    assert recovered.resume_state == "BLOCKED_REQUIRES_RECONCILIATION"
    assert supervisor.retry(job.job_id).status is JobStatus.BLOCKED
    repository.close()


class AlwaysFailReview:
    def run(self, context):
        return StageResult.failed("still failing", metrics={"findings_count": 1})


def test_review_round_limit_blocks_instead_of_looping(tmp_path):
    repository = make_repository(tmp_path)
    supervisor = WorkflowSupervisor(repository, deterministic_runners(AlwaysFailReview()))
    assert supervisor.acquire_singleton()
    job = supervisor.create_demo("owner")
    result = supervisor.run_until_terminal(job.job_id, max_transitions=30)
    assert result.status is JobStatus.BLOCKED
    assert result.resume_state == "MAX_REVIEW_ROUNDS"
    assert result.review_round == result.max_review_rounds == 2
    assert len(repository.latest_stage_runs(job.job_id)) < 30
    supervisor.release_singleton(); repository.close()


class TimeoutRunner:
    def run(self, context):
        return StageResult(StageResultStatus.TIMEOUT, "timed out", error="TIMEOUT")


def test_timeout_retries_are_bounded_by_stage_attempt_limit(tmp_path):
    repository = make_repository(tmp_path)
    runners = deterministic_runners(); runners[WorkflowStage.INTAKE] = TimeoutRunner()
    supervisor = WorkflowSupervisor(repository, runners, retry_backoff_seconds=0)
    assert supervisor.acquire_singleton()
    job = supervisor.create_demo("owner")
    result = supervisor.run_until_terminal(job.job_id)
    assert result.status is JobStatus.FAILED and result.last_error == "TIMEOUT"
    assert repository.stage_attempts(job.job_id, WorkflowStage.INTAKE) == 2
    supervisor.release_singleton(); repository.close()


class BlockingSecurityRunner:
    def run(self, context):
        return StageResult.failed("security policy failed", error="SYNTHETIC_SECURITY_FAILURE")


def test_security_failure_never_advances_to_git_gate(tmp_path):
    repository = make_repository(tmp_path)
    runners = deterministic_runners(); runners[WorkflowStage.SECURITY] = BlockingSecurityRunner()
    supervisor = WorkflowSupervisor(repository, runners)
    assert supervisor.acquire_singleton()
    job = supervisor.create_demo("owner")
    result = supervisor.run_until_terminal(job.job_id)
    assert result.status is JobStatus.BLOCKED and result.current_stage is WorkflowStage.SECURITY
    assert "GIT_GATE" not in [row["stage"] for row in repository.latest_stage_runs(job.job_id)]
    assert "SECURITY_FAILED" in [event["event_type"] for event in repository.events(job.job_id)]
    supervisor.release_singleton(); repository.close()


def test_safe_command_policy_denies_shell_and_path_traversal():
    policy = SafeCommandPolicy()
    allowed = policy.validate(
        (str(CONTROL_PLANE_PYTHON), "-m", "pytest", "-q", "tests/test_control.py"),
        CONTROL_PLANE_ROOT,
    )
    assert allowed[0] == str(CONTROL_PLANE_PYTHON)
    with pytest.raises(PermissionError):
        policy.validate(("bash", "-c", "pytest"), CONTROL_PLANE_ROOT)
    with pytest.raises(PermissionError):
        policy.validate((str(CONTROL_PLANE_PYTHON), "-m", "pytest", "../secrets"), CONTROL_PLANE_ROOT)
    with pytest.raises(PermissionError):
        policy.validate((str(CONTROL_PLANE_PYTHON), "-m", "pytest"), AI_ROOT)
    with pytest.raises(PermissionError):
        policy.validate((str(CONTROL_PLANE_PYTHON), "-m", "pytest", "--maxfail=not-a-number"), CONTROL_PLANE_ROOT)


def test_review_contract_is_structured_bounded_and_path_safe():
    review = ReviewResult("FAIL", (ReviewFinding(
        "BLOCKING", "control-plane/tests/test_workflow_supervisor.py",
        "evidence that is represented only by digest", "recommended revision represented by digest",
    ),))
    result = review.to_stage_result()
    assert result.status is StageResultStatus.FAIL
    assert result.metrics == {"findings_count": 1, "blocking_findings": 1}
    assert result.artifacts[0]["reference"].startswith("review:")
    assert "evidence that" not in result.artifacts[0]["reference"]
    with pytest.raises(PermissionError):
        ReviewResult("FAIL", (ReviewFinding("HIGH", "../other-repo", "x", "y"),)).to_stage_result()
    with pytest.raises(ValueError):
        ReviewResult("MAYBE").to_stage_result()


def test_local_validation_runner_executes_only_allowlisted_argv(tmp_path):
    repository = make_repository(tmp_path)
    job = repository.create_job("validation", "owner")
    context = type("Context", (), {
        "job": job, "stage": WorkflowStage.VALIDATION, "attempt": 1,
        "idempotency_key": "validation", "timeout_seconds": 30, "repository": repository,
    })()
    runner = LocalValidationRunner(
        (str(CONTROL_PLANE_PYTHON), "-m", "pytest", "-q", "tests/test_control.py"),
        timeout_seconds=30,
    )
    result = runner.run(context)
    assert result.status is StageResultStatus.PASS and result.metrics["return_code"] == 0
    denied = LocalValidationRunner(("bash", "-c", "pytest")).run(context)
    assert denied.status is StageResultStatus.BLOCKED
    repository.close()


def test_codex_spec_enforces_scope_and_secret_firewall():
    spec = CodexTaskSpec(
        AI_ROOT, (AI_ROOT / "control-plane",), "safe local task", "LOW", 60, "CODE",
        {"type": "object"},
    )
    persisted = spec.validate()
    assert "task_prompt" not in persisted and len(persisted["task_prompt_sha256"]) == 64
    with pytest.raises(PermissionError):
        CodexTaskSpec(AI_ROOT, (Path("/tmp"),), "safe", "LOW", 60, "CODE", {}).validate()
    with pytest.raises(ValueError):
        unsafe_prompt = "pass" + "word=example-sensitive-value"
        CodexTaskSpec(AI_ROOT, (AI_ROOT,), unsafe_prompt, "LOW", 60, "CODE", {}).validate()
    blocked = RealCodexRunner().run_task(spec)
    assert blocked.status is StageResultStatus.BLOCKED


def test_codex_capability_probe_is_version_help_only(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = "codex 1.0 app-server exec"
        stderr = ""

    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/codex")
    monkeypatch.setattr("subprocess.run", lambda argv, **kwargs: calls.append(tuple(argv)) or Completed())
    result = CodexCapabilityProbe().probe()
    assert result.status == "AVAILABLE" and result.app_server_surface
    assert calls == [("/usr/local/bin/codex", "--version"), ("/usr/local/bin/codex", "--help")]


def test_security_and_git_gate_are_read_only(tmp_path):
    repository = make_repository(tmp_path)
    job = repository.create_job("gate", "owner", metadata={"supervisor_demo": True})
    context = type("Context", (), {
        "job": job, "stage": WorkflowStage.SECURITY, "attempt": 1,
        "idempotency_key": "gate", "timeout_seconds": 30, "repository": repository,
    })()
    assert SecurityRunner().run(context).status is StageResultStatus.PASS
    assert GitGateRunner().run(context).status is StageResultStatus.BLOCKED
    with repository.db:
        for index, stage in enumerate((WorkflowStage.VALIDATION, WorkflowStage.REVIEW, WorkflowStage.SECURITY)):
            repository.db.execute(
                """INSERT INTO supervisor_stage_runs
                   (run_id,job_id,stage,attempt,status,started_at,completed_at,idempotency_key)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (f"gate-{index}", job.job_id, stage.value, 1, "PASS", "now", "now", f"gate-key-{index}"),
            )
    assert GitGateRunner().run(context).status is StageResultStatus.PASS
    repository.close()


def test_health_snapshot_and_bounded_event_payload(tmp_path):
    repository = make_repository(tmp_path)
    job = repository.create_job("health", "owner")
    repository.record_event(job.job_id, "STAGE_COMPLETED", payload={"large": "x" * 5000})
    event = repository.events(job.job_id)[-1]
    assert len(event["payload_json"]) < 500
    health = repository.health_snapshot()
    assert health["status"] == "HEALTHY" and health["db_reachable"]
    repository.close()


def test_supervisor_status_cli_and_exact_pid_scripts(tmp_path):
    scripts = [
        CONTROL_PLANE_ROOT / "scripts/start-supervisor.sh",
        CONTROL_PLANE_ROOT / "scripts/stop-supervisor.sh",
        CONTROL_PLANE_ROOT / "scripts/status-supervisor.sh",
    ]
    for script in scripts:
        source = script.read_text()
        assert os.access(script, os.X_OK)
        assert "local_ai_control.supervisor.app daemon" in source
        assert "pkill" not in source and "killall" not in source and "kill -9" not in source
    environment = {
        "PATH": os.defpath,
        "PYTHONPATH": str(CONTROL_PLANE_ROOT / "src"),
        "LOCAL_AI_SUPERVISOR_DB": str(tmp_path / "cli-supervisor.db"),
    }
    completed = subprocess.run(
        (str(CONTROL_PLANE_PYTHON), "-m", "local_ai_control.supervisor.app", "status"),
        cwd=CONTROL_PLANE_ROOT, env=environment, capture_output=True, text=True,
        timeout=10, shell=False, check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0 and payload["db_reachable"]
    assert payload["active_jobs"] == payload["queue_depth"] == 0


def test_audit_payload_and_stage_text_are_secret_safe(tmp_path):
    repository = make_repository(tmp_path)
    job = repository.create_job("audit", "owner")
    sensitive = "pass" + "word=example-sensitive-value"
    repository.record_event(job.job_id, "STAGE_FAILED", payload={"detail": sensitive})
    event = repository.events(job.job_id)[-1]
    assert sensitive not in event["payload_json"] and event["payload"]["detail"]["redacted"]
    started = repository.begin_stage(job)
    repository.finish_stage(started[0], job.job_id, WorkflowStage.INTAKE,
                            StageResult.failed(sensitive, error=sensitive))
    run = repository.latest_stage_runs(job.job_id)[-1]
    assert run["summary"] == run["error"] == "[REDACTED_BY_SECRET_FIREWALL]"
    repository.close()


def test_terminal_job_retention_removes_children(tmp_path):
    repository = make_repository(tmp_path)
    jobs = [repository.create_job(f"done-{index}", "owner") for index in range(3)]
    for job in jobs:
        repository.update_job(job.job_id, status=JobStatus.COMPLETED, current_stage=WorkflowStage.DONE)
    assert repository.prune_terminal_jobs(keep=1) == 2
    assert len(repository.list_jobs()) == 1
    assert repository.db.execute("SELECT COUNT(*) FROM supervisor_events").fetchone()[0] == 1
    repository.close()
