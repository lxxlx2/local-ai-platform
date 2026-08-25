from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .codex_qwen_workspace import validate_workspace
from .supervisor_contracts import (
    JobStatus,
    MAX_WORK_UNIT_PROMPT_BYTES,
    ReviewFinding,
    ReviewResult,
    WorkflowJob,
    WorkflowStage,
)
from .supervisor_local_qwen import (
    LocalWorktreeCodexTaskSpec,
    LocalWorktreeSupervisorRepository,
    LocalWorktreeWorkflowSupervisor,
    create_local_qwen_job,
    local_qwen_runners,
)


DEFAULT_OPERATOR_RUNTIME = Path("/Users/jerson/AI/runtime/supervisor-local-qwen/operator")
TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.BLOCKED}


def operator_db_path(workspace: str | Path, runtime_root: str | Path = DEFAULT_OPERATOR_RUNTIME) -> Path:
    evidence = validate_workspace(workspace)
    key = hashlib.sha256(f"{evidence.root}\n{evidence.branch}".encode()).hexdigest()[:20]
    return Path(runtime_root).expanduser().resolve() / key / "supervisor.db"


def safe_job_payload(job: WorkflowJob, repository: LocalWorktreeSupervisorRepository | None = None) -> dict:
    payload = {
        "job_id": job.job_id,
        "title": job.title,
        "status": job.status.value,
        "stage": job.current_stage.value,
        "review_round": job.review_round,
        "resume_state": job.resume_state,
        "last_error": job.last_error,
        "workspace": job.project_scope,
    }
    if repository and job.current_stage is WorkflowStage.REVIEW:
        try:
            round_number = job.review_round + 1
            unit = repository.review_work_unit_for_round(job.job_id, job.owner_id, round_number)
            payload["review_work_unit_id"] = unit.review_work_unit_id
            payload["patch_sha256"] = unit.patch_sha256
        except KeyError:
            pass
    return payload


def _load_prompt(path: str) -> str:
    if path == "-":
        data = sys.stdin.read()
    else:
        candidate = Path(path).expanduser().resolve(strict=True)
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("prompt file must be a regular non-symlink file")
        data = candidate.read_text(encoding="utf-8")
    if not data.strip():
        raise ValueError("task prompt is empty")
    if len(data.encode()) > MAX_WORK_UNIT_PROMPT_BYTES:
        raise ValueError("task prompt exceeds safe size bound")
    return data


def _review_unit(repository: LocalWorktreeSupervisorRepository, job: WorkflowJob):
    if job.current_stage is not WorkflowStage.REVIEW:
        raise ValueError("job is not at the Review stage")
    round_number = job.review_round + 1
    return round_number, repository.review_work_unit_for_round(
        job.job_id, job.owner_id, round_number
    )


def _parse_findings(path: str) -> tuple[ReviewFinding, ...]:
    candidate = Path(path).expanduser().resolve(strict=True)
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("findings file must be a regular non-symlink JSON file")
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    raw_findings = payload.get("findings") if isinstance(payload, dict) else payload
    if not isinstance(raw_findings, list) or not raw_findings:
        raise ValueError("findings JSON must contain a non-empty findings list")
    if len(raw_findings) > 100:
        raise ValueError("too many review findings")
    findings = []
    for item in raw_findings:
        if not isinstance(item, dict):
            raise ValueError("review finding must be an object")
        scope = str(item.get("scope", "FILE"))
        severity = str(item.get("severity", ""))
        file_value = item.get("file")
        file_name = str(file_value) if file_value is not None else None
        evidence = str(item.get("evidence", ""))
        recommended_fix = str(item.get("recommended_fix", ""))
        if scope not in {"FILE", "WORKFLOW"}:
            raise ValueError("review finding scope is invalid")
        if severity not in {"BLOCKING", "HIGH", "MEDIUM", "LOW"}:
            raise ValueError("review finding severity is invalid")
        if scope == "FILE" and not file_name:
            raise ValueError("FILE finding requires file")
        if scope == "WORKFLOW" and file_name:
            raise ValueError("WORKFLOW finding cannot contain file")
        if not evidence or not recommended_fix:
            raise ValueError("review finding evidence and recommended_fix are required")
        findings.append(ReviewFinding(severity, file_name, evidence, recommended_fix, scope))
    return tuple(findings)


def _revision_prompt(repository: LocalWorktreeSupervisorRepository, job: WorkflowJob) -> str:
    producer = repository.reconstruct_codex_task(
        job.job_id, job.owner_id, WorkflowStage.PRODUCER, 0
    )
    findings = repository.review_findings(job.job_id, job.owner_id, job.review_round)
    if not findings:
        raise ValueError("revision findings are unavailable")
    lines = [
        "Revision task. Resolve the durable review findings below while preserving the original objective.",
        "Do not commit, push, merge, deploy, access credentials, control services, or use network access.",
        "Run appropriate tests and finish only when the findings are resolved.",
        "",
        "Original objective:",
        producer.task_prompt,
        "",
        "Durable review findings:",
    ]
    for index, finding in enumerate(findings, 1):
        lines.extend([
            f"{index}. scope={finding.scope} severity={finding.severity} file={finding.file or '-'}",
            f"   evidence={finding.evidence}",
            f"   recommended_fix={finding.recommended_fix}",
        ])
    prompt = "\n".join(lines)
    if len(prompt.encode()) > MAX_WORK_UNIT_PROMPT_BYTES:
        raise ValueError("revision prompt exceeds safe size bound")
    return prompt


def ensure_revision_work_unit(repository: LocalWorktreeSupervisorRepository, job: WorkflowJob):
    if job.current_stage is not WorkflowStage.REVISION or job.review_round <= 0:
        return None
    try:
        return repository.work_unit_for_stage(
            job.job_id, job.owner_id, WorkflowStage.REVISION, job.review_round
        )
    except KeyError:
        pass
    producer = repository.reconstruct_codex_task(
        job.job_id, job.owner_id, WorkflowStage.PRODUCER, 0
    )
    spec = LocalWorktreeCodexTaskSpec(
        repository.repo_root,
        producer.allowed_paths,
        _revision_prompt(repository, job),
        job.risk_level,
        producer.timeout_seconds,
        "CODE",
        producer.expected_output_schema,
        write_roots=producer.write_roots,
    )
    return repository.create_work_unit(
        job.job_id,
        job.owner_id,
        WorkflowStage.REVISION,
        spec,
        work_unit_id=f"revision-{job.job_id}-{job.review_round}",
        review_round=job.review_round,
    )


def run_until_boundary(
    supervisor: LocalWorktreeWorkflowSupervisor,
    repository: LocalWorktreeSupervisorRepository,
    job_id: str,
    *,
    max_steps: int = 32,
) -> WorkflowJob:
    for _ in range(max_steps):
        job = repository.get_job(job_id)
        if job.status in TERMINAL:
            return job
        if (
            job.current_stage is WorkflowStage.REVIEW
            and job.status is JobStatus.WAITING
            and job.resume_state == "REVIEW_RESULT_PENDING"
        ):
            return job
        if job.current_stage is WorkflowStage.REVISION and job.status is JobStatus.QUEUED:
            ensure_revision_work_unit(repository, job)
        updated = supervisor.run_job_once(job_id)
        if updated is None:
            return repository.get_job(job_id)
    raise RuntimeError("operator transition limit reached")


def _open_repository(workspace: str, db_value: str | None):
    evidence = validate_workspace(workspace)
    db = Path(db_value).expanduser().resolve() if db_value else operator_db_path(evidence.root)
    repository = LocalWorktreeSupervisorRepository(evidence.root, db)
    repository.migrate()
    return evidence, repository, db


def _run_enabled(repository: LocalWorktreeSupervisorRepository, job_id: str) -> WorkflowJob:
    supervisor = LocalWorktreeWorkflowSupervisor(
        repository,
        local_qwen_runners(repository.repo_root, enabled=True),
        timeout_seconds=900,
    )
    if not supervisor.acquire_singleton():
        raise RuntimeError("another local Qwen Supervisor consumer owns the operator database")
    try:
        return run_until_boundary(supervisor, repository, job_id)
    finally:
        supervisor.release_singleton()


def command_submit(args) -> dict:
    _evidence, repository, db = _open_repository(args.workspace, args.db)
    try:
        prompt = _load_prompt(args.prompt_file)
        job, _unit = create_local_qwen_job(
            repository,
            title=args.title,
            owner_id=args.owner,
            task_prompt=prompt,
            risk_level=args.risk,
            timeout_seconds=args.timeout,
            job_id=args.job_id,
        )
        result = _run_enabled(repository, job.job_id)
        return safe_job_payload(result, repository) | {"db": str(db)}
    finally:
        repository.close()


def command_status(args) -> dict:
    _evidence, repository, db = _open_repository(args.workspace, args.db)
    try:
        job = repository.get_job_for_owner(args.job, args.owner)
        return safe_job_payload(job, repository) | {"db": str(db)}
    finally:
        repository.close()


def command_review_show(args) -> dict:
    _evidence, repository, db = _open_repository(args.workspace, args.db)
    try:
        job = repository.get_job_for_owner(args.job, args.owner)
        round_number, unit = _review_unit(repository, job)
        patch = repository.reconstruct_reviewer_patch(job.job_id, job.owner_id, round_number)
        result = {
            "job_id": job.job_id,
            "review_round": round_number,
            "review_work_unit_id": unit.review_work_unit_id,
            "patch_sha256": unit.patch_sha256,
            "candidate_identity_sha256": unit.candidate_identity_sha256,
            "db": str(db),
        }
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            output.write_text(patch, encoding="utf-8")
            os.chmod(output, 0o600)
            result["patch_file"] = str(output)
        else:
            result["patch"] = patch
        return result
    finally:
        repository.close()


def command_review_pass(args) -> dict:
    _evidence, repository, db = _open_repository(args.workspace, args.db)
    try:
        job = repository.get_job_for_owner(args.job, args.owner)
        round_number, unit = _review_unit(repository, job)
        submission = repository.submit_review_result(
            job.job_id,
            job.owner_id,
            round_number,
            unit.review_work_unit_id,
            ReviewResult("PASS"),
        )
        return {
            "job_id": job.job_id,
            "review_round": round_number,
            "review_work_unit_id": unit.review_work_unit_id,
            "review_status": submission.status,
            "result_hash": submission.result_hash,
            "db": str(db),
        }
    finally:
        repository.close()


def command_review_fail(args) -> dict:
    _evidence, repository, db = _open_repository(args.workspace, args.db)
    try:
        job = repository.get_job_for_owner(args.job, args.owner)
        round_number, unit = _review_unit(repository, job)
        findings = _parse_findings(args.findings_file)
        submission = repository.submit_review_result(
            job.job_id,
            job.owner_id,
            round_number,
            unit.review_work_unit_id,
            ReviewResult("FAIL", findings),
        )
        return {
            "job_id": job.job_id,
            "review_round": round_number,
            "review_work_unit_id": unit.review_work_unit_id,
            "review_status": submission.status,
            "findings_count": len(findings),
            "result_hash": submission.result_hash,
            "db": str(db),
        }
    finally:
        repository.close()


def command_continue(args) -> dict:
    _evidence, repository, db = _open_repository(args.workspace, args.db)
    try:
        job = repository.get_job_for_owner(args.job, args.owner)
        if (
            job.current_stage is WorkflowStage.REVIEW
            and job.status is JobStatus.WAITING
            and job.resume_state == "REVIEW_RESULT_PENDING"
        ):
            round_number, unit = _review_unit(repository, job)
            repository.submitted_review_result(
                job.job_id, job.owner_id, round_number, unit.review_work_unit_id
            )
        result = _run_enabled(repository, job.job_id)
        return safe_job_payload(result, repository) | {"db": str(db)}
    finally:
        repository.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Qwen Supervisor operator entry")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--db")
    parser.add_argument("--owner", default=os.environ.get("LOCAL_QWEN_OWNER_ID", "local-owner"))
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit")
    submit.add_argument("--title", default="LOCAL_QWEN_TASK")
    submit.add_argument("--prompt-file", required=True, help="UTF-8 file or '-' for stdin")
    submit.add_argument("--risk", default="LOW", choices=("LOW", "MEDIUM", "HIGH"))
    submit.add_argument("--timeout", type=float, default=900)
    submit.add_argument("--job-id")
    submit.set_defaults(handler=command_submit)

    status = sub.add_parser("status")
    status.add_argument("--job", required=True)
    status.set_defaults(handler=command_status)

    show = sub.add_parser("review-show")
    show.add_argument("--job", required=True)
    show.add_argument("--output")
    show.set_defaults(handler=command_review_show)

    approve = sub.add_parser("review-pass")
    approve.add_argument("--job", required=True)
    approve.set_defaults(handler=command_review_pass)

    reject = sub.add_parser("review-fail")
    reject.add_argument("--job", required=True)
    reject.add_argument("--findings-file", required=True)
    reject.set_defaults(handler=command_review_fail)

    resume = sub.add_parser("continue")
    resume.add_argument("--job", required=True)
    resume.set_defaults(handler=command_continue)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
    except Exception as error:
        print(json.dumps({"status": "ERROR", "error": type(error).__name__, "message": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
