from __future__ import annotations

from . import generic_project_operator as base
from .generic_project_guarded import guarded_generic_project_runners
from .generic_project_policy import TestProfile
from .generic_project_repository_guarded import GuardedGenericProjectSupervisorRepository
from .supervisor_contracts import JobStatus, StageResultStatus, WorkflowStage, _safe_text
from .supervisor_generic_project import GenericProjectWorkflowSupervisor


class GuardedGenericProjectWorkflowSupervisor(GenericProjectWorkflowSupervisor):
    """Generic workflow with phase-aware BLOCKED semantics."""

    def _schedule_failure(self, job, result):
        if (
            result.status is StageResultStatus.BLOCKED
            and (result.metrics or {}).get("execution_started") is False
        ):
            error = _safe_text(result.error or result.summary, 1000)
            updated = self.repository.update_job(
                job.job_id,
                status=JobStatus.BLOCKED,
                resume_state="PREEXECUTION_BLOCKED",
                last_error=error,
            )
            self.repository.record_event(
                job.job_id,
                "STAGE_FAILED",
                job.current_stage,
                {"error": error, "execution_started": False},
            )
            return updated
        return super()._schedule_failure(job, result)


def _review_result_is_ready(repository, job) -> bool:
    if not (
        job.current_stage is WorkflowStage.REVIEW
        and job.status is JobStatus.WAITING
        and job.resume_state == "REVIEW_RESULT_PENDING"
    ):
        return False
    round_number = job.review_round + 1
    try:
        unit = repository.review_work_unit_for_round(
            job.job_id,
            job.owner_id,
            round_number,
        )
        repository.submitted_review_result(
            job.job_id,
            job.owner_id,
            round_number,
            unit.review_work_unit_id,
        )
    except KeyError:
        return False
    return True


def _run_enabled(repository, job_id: str, test_profile: TestProfile):
    runners = guarded_generic_project_runners(
        repository.repo_root,
        enabled=True,
        test_profile=test_profile,
    )
    supervisor = GuardedGenericProjectWorkflowSupervisor(
        repository,
        runners,
        timeout_seconds=900,
    )
    if not supervisor.acquire_singleton():
        raise RuntimeError("another generic project Supervisor consumer owns this task DB")
    try:
        current = repository.get_job(job_id)
        if _review_result_is_ready(repository, current):
            supervisor.run_job_once(job_id)

        job = base._run_until_boundary(supervisor, repository, job_id)
        if (
            job.current_stage is WorkflowStage.REVIEW
            and job.status is JobStatus.WAITING
            and job.resume_state == "REVIEW_RESULT_PENDING"
        ):
            runners[WorkflowStage.REVIEW].ensure_advisory(repository, job)
            job = repository.get_job(job_id)
        return job
    finally:
        supervisor.release_singleton()


def main(argv: list[str] | None = None) -> int:
    base.GenericProjectSupervisorRepository = GuardedGenericProjectSupervisorRepository
    base._run_enabled = _run_enabled
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
