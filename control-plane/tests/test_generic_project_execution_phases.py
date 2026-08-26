from __future__ import annotations

from types import SimpleNamespace

from local_ai_control.services.generic_project_persisted import GenericPersistedStageRunner
from local_ai_control.services.supervisor_contracts import StageResultStatus, WorkflowStage


class NeverTaskRunner:
    cancellation_supported = True

    def run_task(self, spec, execution_id):
        raise AssertionError("task runner must not start")

    def cancel(self, execution_id=None, reason=None):
        return True


class InvalidExecutionSpec:
    def execution_view(self):
        raise ValueError("fixture execution view invalid")


class PreparationRepository:
    def __init__(self):
        self.start_calls = 0

    def work_unit_for_stage(self, job_id, owner_id, stage, review_round):
        return SimpleNamespace(work_unit_id="producer-job-1")

    def reconstruct_codex_task(self, job_id, owner_id, stage, review_round):
        return InvalidExecutionSpec()

    def start_execution(self, *args, **kwargs):
        self.start_calls += 1


def test_invalid_execution_view_never_marks_execution_started():
    repository = PreparationRepository()
    context = SimpleNamespace(
        stage=WorkflowStage.PRODUCER,
        job=SimpleNamespace(job_id="job-1", owner_id="owner", review_round=0),
        repository=repository,
        idempotency_key="job-1:PRODUCER:1",
    )
    runner = GenericPersistedStageRunner(NeverTaskRunner())

    result = runner.run(context)

    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "GENERIC_EXECUTION_VIEW_INVALID"
    assert result.metrics["category"] == "ValueError"
    assert result.metrics["execution_started"] is False
    assert repository.start_calls == 0
