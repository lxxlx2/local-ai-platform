from __future__ import annotations

import json

from . import generic_project_operator as base
from .generic_project_guarded import guarded_generic_project_runners
from .generic_project_policy import TestProfile
from .generic_project_repository_guarded import GuardedGenericProjectSupervisorRepository
from .supervisor_contracts import JobStatus, StageResultStatus, WorkflowStage, _safe_text
from .supervisor_generic_project import GenericProjectWorkflowSupervisor


_BASE_SAFE_PAYLOAD = base._safe_payload


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


def _safe_payload_guarded(record: dict, repository, job) -> dict:
    payload = _BASE_SAFE_PAYLOAD(record, repository, job)
    if job.status is not JobStatus.BLOCKED or job.resume_state != "PREEXECUTION_BLOCKED":
        return payload
    runs = repository.latest_stage_runs(job.job_id)
    if not runs:
        return payload
    latest = runs[-1]
    try:
        metrics = json.loads(latest.get("metrics_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        metrics = {}
    diagnostic = {
        "stage": str(latest.get("stage") or job.current_stage.value),
        "error": str(latest.get("error") or job.last_error or ""),
        "category": str(metrics.get("category") or ""),
        "detail": _safe_text(str(metrics.get("detail") or metrics.get("quota_probe_error") or ""), 1000) or "",
        "execution_started": bool(metrics.get("execution_started", False)),
    }
    payload["preexecution_diagnostic"] = diagnostic
    return payload


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
    base._safe_payload = _safe_payload_guarded
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
