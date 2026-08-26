from __future__ import annotations

from types import SimpleNamespace

from local_ai_control.services.generic_project_guarded import GuardedDirectGenericProjectQwenRunner
from local_ai_control.services.generic_project_persisted import GenericPersistedStageRunner
from local_ai_control.services.supervisor_contracts import StageResult, StageResultStatus, WorkflowStage


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


class BrokenQuotaGuard:
    def before(self):
        raise ValueError("fixture quota parser failure")


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


def test_raw_quota_probe_value_error_is_normalized_before_agent_start():
    runner = GuardedDirectGenericProjectQwenRunner(
        enabled=True,
        quota_guard=BrokenQuotaGuard(),
    )

    result = runner.run_task(None, "00000000-0000-4000-8000-000000000001")

    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "CODEX_QUOTA_PRECHECK_UNAVAILABLE"
    assert result.metrics["category"] == "ValueError"
    assert result.metrics["codex_cli_invoked"] is False
