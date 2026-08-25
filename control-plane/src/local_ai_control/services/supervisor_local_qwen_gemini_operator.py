from __future__ import annotations

from . import supervisor_local_qwen_operator as operator
from .supervisor_gemini_review import gemini_local_qwen_runners
from .supervisor_local_qwen import LocalWorktreeWorkflowSupervisor


def _run_enabled(repository, job_id):
    supervisor = LocalWorktreeWorkflowSupervisor(
        repository,
        gemini_local_qwen_runners(repository.repo_root, enabled=True),
        timeout_seconds=900,
    )
    if not supervisor.acquire_singleton():
        raise RuntimeError("another local Qwen Supervisor consumer owns the operator database")
    try:
        return operator.run_until_boundary(supervisor, repository, job_id)
    finally:
        supervisor.release_singleton()


def main() -> None:
    operator._run_enabled = _run_enabled
    operator.main()


if __name__ == "__main__":
    main()
