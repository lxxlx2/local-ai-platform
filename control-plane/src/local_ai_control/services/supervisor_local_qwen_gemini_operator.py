from __future__ import annotations

from . import supervisor_local_qwen_operator as operator
from .supervisor_contracts import JobStatus, WorkflowStage
from .supervisor_gemini_review import gemini_local_qwen_runners, load_gemini_recommendation
from .supervisor_local_qwen import LocalWorktreeWorkflowSupervisor


_BASE_SAFE_JOB_PAYLOAD = operator.safe_job_payload
_BASE_COMMAND_REVIEW_SHOW = operator.command_review_show


def _gemini_summary(recommendation: dict | None) -> dict | None:
    if not recommendation:
        return None
    payload = {
        "status": recommendation.get("status"),
        "patch_sha256": recommendation.get("patch_sha256"),
    }
    if recommendation.get("status") == "READY":
        payload.update(
            {
                "verdict": recommendation.get("verdict"),
                "summary": recommendation.get("summary"),
                "findings_count": recommendation.get("findings_count", 0),
                "model": recommendation.get("model"),
                "privacy": recommendation.get("privacy"),
                "redactions": recommendation.get("redactions", []),
            }
        )
    return payload


def safe_job_payload(job, repository=None):
    payload = _BASE_SAFE_JOB_PAYLOAD(job, repository)
    if repository and payload.get("review_work_unit_id"):
        recommendation = load_gemini_recommendation(
            repository, payload["review_work_unit_id"]
        )
        summary = _gemini_summary(recommendation)
        if summary is not None:
            payload["gemini_advisory"] = summary
    return payload


def command_review_show(args):
    result = _BASE_COMMAND_REVIEW_SHOW(args)
    _evidence, repository, _db = operator._open_repository(args.workspace, args.db)
    try:
        recommendation = load_gemini_recommendation(
            repository, result["review_work_unit_id"]
        )
        summary = _gemini_summary(recommendation)
        if summary is not None:
            result["gemini_advisory"] = summary
            if recommendation and recommendation.get("status") == "READY":
                result["gemini_findings"] = recommendation.get("findings", [])
    finally:
        repository.close()
    return result


def _run_enabled(repository, job_id):
    runners = gemini_local_qwen_runners(repository.repo_root, enabled=True)
    supervisor = LocalWorktreeWorkflowSupervisor(
        repository,
        runners,
        timeout_seconds=900,
    )
    if not supervisor.acquire_singleton():
        raise RuntimeError("another local Qwen Supervisor consumer owns the operator database")
    try:
        job = operator.run_until_boundary(supervisor, repository, job_id)
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


def main() -> None:
    operator.safe_job_payload = safe_job_payload
    operator.command_review_show = command_review_show
    operator._run_enabled = _run_enabled
    operator.main()


if __name__ == "__main__":
    main()
