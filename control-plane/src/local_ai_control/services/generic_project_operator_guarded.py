from __future__ import annotations

from . import generic_project_operator as base
from .generic_project_guarded import guarded_generic_project_runners
from .generic_project_policy import TestProfile
from .generic_project_repository_guarded import GuardedGenericProjectSupervisorRepository
from .supervisor_generic_project import GenericProjectWorkflowSupervisor


def _run_enabled(repository, job_id: str, test_profile: TestProfile):
    supervisor = GenericProjectWorkflowSupervisor(
        repository,
        guarded_generic_project_runners(
            repository.repo_root,
            enabled=True,
            test_profile=test_profile,
        ),
        timeout_seconds=900,
    )
    if not supervisor.acquire_singleton():
        raise RuntimeError("another generic project Supervisor consumer owns this task DB")
    try:
        return base._run_until_boundary(supervisor, repository, job_id)
    finally:
        supervisor.release_singleton()


def main(argv: list[str] | None = None) -> int:
    base.GenericProjectSupervisorRepository = GuardedGenericProjectSupervisorRepository
    base._run_enabled = _run_enabled
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
