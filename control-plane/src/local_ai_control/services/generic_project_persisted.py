from __future__ import annotations

import uuid

from .supervisor_codex import PersistedCodexStageRunner
from .supervisor_contracts import StageContext, StageResult, StageResultStatus, WorkflowStage


class GenericTaskRunnerExecutionUncertain(RuntimeError):
    pass


class GenericExecutionCompletionUncertain(RuntimeError):
    pass


def _safe_detail(error: Exception) -> str:
    return str(error).replace("\n", " ")[:400]


class GenericPersistedStageRunner(PersistedCodexStageRunner):
    """Persisted mutating runner with phase-correct fail-closed semantics.

    Durable execution is not marked STARTED until the immutable execution view
    has been reconstructed and validated. Once execution has actually started,
    unexpected runner/completion exceptions still create the mutation fence.
    """

    def run(self, context: StageContext) -> StageResult:
        if context.stage not in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Generic mutating stage runner scope denied",
                error="GENERIC_STAGE_SCOPE",
            )
        if context.stage is WorkflowStage.REVISION and not context.current_review_findings():
            return StageResult(
                StageResultStatus.BLOCKED,
                "Revision findings unavailable",
                error="REVISION_FINDINGS_NOT_AVAILABLE",
            )

        try:
            unit = context.repository.work_unit_for_stage(
                context.job.job_id,
                context.job.owner_id,
                context.stage,
                context.job.review_round if context.stage is WorkflowStage.REVISION else 0,
            )
            spec = context.repository.reconstruct_codex_task(
                context.job.job_id,
                context.job.owner_id,
                context.stage,
                context.job.review_round if context.stage is WorkflowStage.REVISION else 0,
            )
            execution_spec = spec.execution_view()
        except Exception as error:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Generic immutable execution view is unavailable or invalid",
                error="GENERIC_EXECUTION_VIEW_INVALID",
                metrics={
                    "category": type(error).__name__,
                    "detail": _safe_detail(error),
                    "execution_started": False,
                },
            )

        self.execution_id = str(uuid.uuid4())
        self.work_unit_id = unit.work_unit_id
        self.repository = context.repository
        provider = type(self.task_runner).__name__

        try:
            context.repository.start_execution(
                self.execution_id,
                context.job.job_id,
                unit.work_unit_id,
                context.stage,
                context.idempotency_key,
                provider,
            )
        except Exception as error:
            self.execution_id = None
            self.work_unit_id = None
            self.repository = None
            return StageResult(
                StageResultStatus.BLOCKED,
                "Durable generic execution start denied",
                error="GENERIC_EXECUTION_START_DENIED",
                metrics={
                    "category": type(error).__name__,
                    "detail": _safe_detail(error),
                    "execution_started": False,
                },
            )

        try:
            try:
                result = self.task_runner.run_task(execution_spec, self.execution_id)
            except Exception as error:
                context.repository.persist_mutation_fence(
                    context.job.job_id,
                    "GENERIC_TASK_RUNNER_EXECUTION_UNCERTAIN",
                    unit.work_unit_id,
                    self.execution_id,
                )
                raise GenericTaskRunnerExecutionUncertain(
                    f"{type(error).__name__}: {_safe_detail(error)}"
                ) from error

            try:
                completion = context.repository.complete_execution(self.execution_id, result)
            except Exception as error:
                context.repository.persist_mutation_fence(
                    context.job.job_id,
                    "GENERIC_EXECUTION_COMPLETION_UNCERTAIN",
                    unit.work_unit_id,
                    self.execution_id,
                )
                raise GenericExecutionCompletionUncertain(
                    f"{type(error).__name__}: {_safe_detail(error)}"
                ) from error

            if (
                result.status is StageResultStatus.PASS
                and completion["completion_status"] == "UNKNOWN"
                and context.repository.has_active_mutation_fence()
            ):
                return StageResult(
                    StageResultStatus.BLOCKED,
                    "Execution completion could not be safely confirmed",
                    error="EXECUTION_COMPLETION_UNCONFIRMED",
                )
            return result
        finally:
            self.execution_id = None
            self.work_unit_id = None
            self.repository = None
