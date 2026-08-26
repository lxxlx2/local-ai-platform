from __future__ import annotations

from types import SimpleNamespace

from local_ai_control.services.generic_project_guarded import GuardedDirectGenericProjectQwenRunner
from local_ai_control.services.generic_project_operator_guarded import GuardedGenericProjectWorkflowSupervisor
from local_ai_control.services.generic_project_persisted import GenericPersistedStageRunner
from local_ai_control.services.supervisor_contracts import (
    JobStatus,
    StageResult,
    StageResultStatus,
    WorkflowStage,
)


class NeverTaskRunner:
    cancellation_supported = True

    def run_task(self, spec, execution_id):
        raise AssertionError("task runner must not start")

    def cancel(self, execution_id=None, reason=None):
        return True


class InvalidExecutionSpec:
    def validate(self):
        raise ValueError("fixture immutable spec invalid")

    def execution_view(self):
        raise AssertionError("legacy execution_view must not be called")


class ValidDirectSpec:
    def validate(self):
        return {"safe_file_manifest": [{"path": "app.py", "sha256": "x", "size_bytes": 1}]}

    def execution_view(self):
        raise AssertionError("legacy execution_view must not be called")


class PreparationRepository:
    def __init__(self, spec=None):
        self.start_calls = 0
        self.complete_calls = 0
        self.spec = spec or InvalidExecutionSpec()

    def work_unit_for_stage(self, job_id, owner_id, stage, review_round):
        return SimpleNamespace(work_unit_id="producer-job-1")

    def reconstruct_codex_task(self, job_id, owner_id, stage, review_round):
        return self.spec

    def start_execution(self, *args, **kwargs):
        self.start_calls += 1

    def complete_execution(self, execution_id, result):
        self.complete_calls += 1
        return {"completion_status": "COMPLETED_CONFIRMED"}

    def has_active_mutation_fence(self):
        return False

    def persist_mutation_fence(self, *args, **kwargs):
        raise AssertionError("no mutation fence expected")


class PassTaskRunner:
    cancellation_supported = True

    def __init__(self):
        self.received = None

    def run_task(self, spec, execution_id):
        self.received = spec
        return StageResult.passed("fixture pass")

    def cancel(self, execution_id=None, reason=None):
        return True


class RemoteProvider:
    base_url = "https://api.example.invalid"

    def health(self):
        raise AssertionError("remote provider health must not be called")

    def generate(self, *args, **kwargs):
        raise AssertionError("remote provider generation must not be called")


class ExplodingQuotaGuard:
    def before(self):
        raise AssertionError("quota telemetry must stay out of the direct task lifecycle")

    def after(self, before):
        raise AssertionError("quota telemetry must stay out of the direct task lifecycle")


class FailureSchedulingRepository:
    def __init__(self):
        self.updates = []
        self.events = []

    def update_job(self, job_id, **kwargs):
        self.updates.append((job_id, kwargs))
        return SimpleNamespace(job_id=job_id, **kwargs)

    def record_event(self, job_id, event_type, stage, payload):
        self.events.append((job_id, event_type, stage, payload))


def _context(repository):
    return SimpleNamespace(
        stage=WorkflowStage.PRODUCER,
        job=SimpleNamespace(job_id="job-1", owner_id="owner", review_round=0),
        repository=repository,
        idempotency_key="job-1:PRODUCER:1",
    )


def test_invalid_immutable_spec_never_marks_execution_started():
    repository = PreparationRepository()
    runner = GenericPersistedStageRunner(NeverTaskRunner())

    result = runner.run(_context(repository))

    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "GENERIC_EXECUTION_SPEC_INVALID"
    assert result.metrics["category"] == "ValueError"
    assert result.metrics["execution_started"] is False
    assert repository.start_calls == 0


def test_valid_direct_spec_bypasses_legacy_codex_execution_view():
    spec = ValidDirectSpec()
    repository = PreparationRepository(spec)
    task_runner = PassTaskRunner()
    runner = GenericPersistedStageRunner(task_runner)

    result = runner.run(_context(repository))

    assert result.status is StageResultStatus.PASS
    assert task_runner.received is spec
    assert repository.start_calls == 1
    assert repository.complete_calls == 1


def test_remote_provider_route_is_denied_before_any_generation_or_quota_probe():
    runner = GuardedDirectGenericProjectQwenRunner(
        enabled=True,
        provider=RemoteProvider(),
        quota_guard=ExplodingQuotaGuard(),
    )

    result = runner.run_task(None, "00000000-0000-4000-8000-000000000001")

    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "LOCAL_EXECUTOR_ROUTE_DENIED"
    assert result.metrics["local_route_attestation"] == "FAIL"
    assert result.metrics["codex_cli_invoked"] is False
    assert result.metrics["codex_app_server_invoked"] is False
    assert result.metrics["execution_started"] is False


def test_started_mutating_failure_is_blocked_without_automatic_retry():
    repository = FailureSchedulingRepository()
    supervisor = GuardedGenericProjectWorkflowSupervisor(repository, {}, timeout_seconds=900)
    job = SimpleNamespace(job_id="job-1", current_stage=WorkflowStage.PRODUCER)
    result = StageResult(
        StageResultStatus.FAIL,
        "agent protocol failed after mutation",
        error="DIRECT_LOCAL_QWEN_DirectLocalQwenProtocolError",
        metrics={
            "execution_started": True,
            "candidate_diff_nonempty": True,
            "finalization_verified": False,
        },
    )

    updated = supervisor._schedule_failure(job, result)

    assert updated.status is JobStatus.BLOCKED
    assert updated.resume_state == "MUTATING_EXECUTION_FAILED_REVIEW_REQUIRED"
    assert repository.updates[-1][1]["status"] is JobStatus.BLOCKED
    assert repository.events[-1][3]["automatic_retry"] is False
    assert repository.events[-1][3]["candidate_diff_nonempty"] is True
