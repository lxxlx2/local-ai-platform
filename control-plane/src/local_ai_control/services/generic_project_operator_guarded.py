from __future__ import annotations

from . import generic_project_operator as base
from .generic_project_guarded import guarded_generic_project_runners
from .generic_project_policy import TestProfile
from .generic_project_repository_guarded import GuardedGenericProjectSupervisorRepository
from .supervisor_contracts import JobStatus, WorkflowStage
from .supervisor_generic_project import GenericProjectWorkflowSupervisor


def _run_enabled(repository, job_id: str, test_profile: TestProfile):
    runners = guarded_generic_project_runners(
        repository.repo_root,
        enabled=True,
        test_profile=test_profile,
    )
    supervisor = GenericProjectWorkflowSupervisor(
        repository,
        runners,
        timeout_seconds=900,
    )
    if not supervisor.acquire_singleton():
        raise RuntimeError("another generic project Supervisor consumer owns this task DB")
    try:
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