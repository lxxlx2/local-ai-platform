from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from .generic_project_adapter import (
    DEFAULT_GENERIC_PROJECT_RUNTIME,
    GenericProjectRegistry,
    PROJECT_ID_RE,
    TASK_ID_RE,
)
from .generic_project_policy import TestProfile
from .provider_router import PrivacyMode
from .supervisor_contracts import (
    JobStatus,
    MAX_WORK_UNIT_PROMPT_BYTES,
    ReviewResult,
    WorkflowStage,
    ensure_private_directory,
    ensure_private_file,
)
from .supervisor_generic_project import (
    GenericProjectCodexTaskSpec,
    GenericProjectSupervisorRepository,
    GenericProjectWorkflowSupervisor,
    create_generic_qwen_job,
    generic_project_runners,
)
from .supervisor_gemini_review import load_gemini_recommendation
from .supervisor_local_qwen_gemini_operator import _gemini_summary
from .supervisor_local_qwen_operator import _parse_findings, _revision_prompt


TASK_RECORD_SCHEMA = "0.1"
TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED, JobStatus.BLOCKED}


def _runtime_root(value: str | Path | None) -> Path:
    return ensure_private_directory(Path(value or DEFAULT_GENERIC_PROJECT_RUNTIME).expanduser().resolve())


def _task_dir(runtime_root: Path, project_id: str, task_id: str) -> Path:
    if not PROJECT_ID_RE.fullmatch(project_id) or not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("invalid generic project/task id")
    return ensure_private_directory(runtime_root / "tasks" / project_id / task_id)


def _task_record_path(runtime_root: Path, project_id: str, task_id: str) -> Path:
    return _task_dir(runtime_root, project_id, task_id) / "task.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        ensure_private_file(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_record(runtime_root: Path, project_id: str, task_id: str) -> dict:
    path = _task_record_path(runtime_root, project_id, task_id)
    if path.is_symlink() or not path.is_file():
        raise ValueError("generic project task record is unavailable")
    ensure_private_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("generic project task record is unreadable") from error
    required = {
        "schema_version", "project_id", "task_id", "worktree_root", "branch",
        "base_commit_sha", "test_profile", "privacy_mode", "db", "job_id",
    }
    if not isinstance(payload, dict) or not required <= set(payload):
        raise ValueError("generic project task record schema is invalid")
    if payload["schema_version"] != TASK_RECORD_SCHEMA:
        raise ValueError("unsupported generic project task record schema")
    if payload["project_id"] != project_id or payload["task_id"] != task_id:
        raise ValueError("generic project task record identity mismatch")
    return payload


def _load_prompt(path: str) -> str:
    if path == "-":
        text = sys.stdin.read()
    else:
        raw = Path(path).expanduser()
        if raw.is_symlink():
            raise ValueError("prompt file must not be a symlink")
        candidate = raw.resolve(strict=True)
        if not candidate.is_file():
            raise ValueError("prompt file must be a regular file")
        text = candidate.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("task prompt is empty")
    if len(text.encode()) > MAX_WORK_UNIT_PROMPT_BYTES:
        raise ValueError("task prompt exceeds safe size bound")
    return text


def _open_task(runtime_root: Path, project_id: str, task_id: str):
    record = _load_record(runtime_root, project_id, task_id)
    worktree = Path(record["worktree_root"]).resolve(strict=True)
    db = Path(record["db"]).resolve()
    expected_db_parent = _task_dir(runtime_root, project_id, task_id).resolve()
    if db.parent != expected_db_parent or db.name != "supervisor.db":
        raise PermissionError("generic project task DB path is not bound to task runtime")
    repository = GenericProjectSupervisorRepository(worktree, db)
    repository.migrate()
    job = repository.get_job(record["job_id"])
    if Path(job.project_scope).resolve() != worktree:
        repository.close()
        raise PermissionError("generic project durable job scope mismatch")
    return record, repository, job


def _advisory(repository, job) -> dict | None:
    if job.current_stage is not WorkflowStage.REVIEW:
        return None
    try:
        round_number = job.review_round + 1
        unit = repository.review_work_unit_for_round(job.job_id, job.owner_id, round_number)
    except KeyError:
        return None
    return _gemini_summary(load_gemini_recommendation(repository, unit.review_work_unit_id))


def _safe_payload(record: dict, repository, job) -> dict:
    payload = {
        "project_id": record["project_id"],
        "task_id": record["task_id"],
        "job_id": job.job_id,
        "status": job.status.value,
        "stage": job.current_stage.value,
        "review_round": job.review_round,
        "resume_state": job.resume_state,
        "last_error": job.last_error,
        "worktree": record["worktree_root"],
        "branch": record["branch"],
        "test_profile": record["test_profile"],
        "privacy_mode": record["privacy_mode"],
    }
    if job.current_stage is WorkflowStage.REVIEW:
        try:
            round_number = job.review_round + 1
            unit = repository.review_work_unit_for_round(job.job_id, job.owner_id, round_number)
            payload["review_work_unit_id"] = unit.review_work_unit_id
            payload["patch_sha256"] = unit.patch_sha256
        except KeyError:
            pass
        advisory = _advisory(repository, job)
        if advisory is not None:
            payload["gemini_advisory"] = advisory
    return payload


def _ensure_revision_work_unit(repository: GenericProjectSupervisorRepository, job):
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
    spec = GenericProjectCodexTaskSpec(
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


def _run_until_boundary(supervisor, repository, job_id: str, *, max_steps: int = 32):
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
            _ensure_revision_work_unit(repository, job)
        updated = supervisor.run_job_once(job_id)
        if updated is None:
            return repository.get_job(job_id)
    raise RuntimeError("generic project transition limit reached")


def _run_enabled(repository, job_id: str, test_profile: TestProfile):
    supervisor = GenericProjectWorkflowSupervisor(
        repository,
        generic_project_runners(
            repository.repo_root,
            enabled=True,
            test_profile=test_profile,
        ),
        timeout_seconds=900,
    )
    if not supervisor.acquire_singleton():
        raise RuntimeError("another generic project Supervisor consumer owns this task DB")
    try:
        return _run_until_boundary(supervisor, repository, job_id)
    finally:
        supervisor.release_singleton()


def command_register(args) -> dict:
    registry = GenericProjectRegistry(_runtime_root(args.runtime))
    record = registry.register(args.repo, project_id=args.project_id)
    return {
        "project_id": record.project_id,
        "source_root": record.source_root,
        "source_head_sha": record.source_head_sha,
        "detected_test_profile": record.detected_test_profile,
    }


def command_list(args) -> dict:
    registry = GenericProjectRegistry(_runtime_root(args.runtime))
    return {
        "projects": [
            {
                "project_id": item.project_id,
                "source_root": item.source_root,
                "source_head_sha": item.source_head_sha,
                "detected_test_profile": item.detected_test_profile,
            }
            for item in registry.list_projects()
        ]
    }


def command_task(args) -> dict:
    runtime = _runtime_root(args.runtime)
    registry = GenericProjectRegistry(runtime)
    profile = TestProfile(args.test_profile) if args.test_profile else None
    privacy = PrivacyMode(args.privacy)
    worktree = registry.create_task_worktree(
        args.project,
        args.task_id,
        base_ref=args.base_ref,
        test_profile=profile,
    )
    task_dir = _task_dir(runtime, args.project, args.task_id)
    db = task_dir / "supervisor.db"
    repository = GenericProjectSupervisorRepository(Path(worktree.worktree_root), db)
    repository.migrate()
    try:
        prompt = _load_prompt(args.prompt_file)
        job, _unit = create_generic_qwen_job(
            repository,
            title=args.title,
            owner_id=args.owner,
            task_prompt=prompt,
            test_profile=TestProfile(worktree.test_profile),
            privacy_mode=privacy,
            risk_level=args.risk,
            timeout_seconds=args.timeout,
        )
        record = {
            "schema_version": TASK_RECORD_SCHEMA,
            "project_id": args.project,
            "task_id": args.task_id,
            "source_root": worktree.source_root,
            "worktree_root": worktree.worktree_root,
            "branch": worktree.branch,
            "base_commit_sha": worktree.base_commit_sha,
            "test_profile": worktree.test_profile,
            "privacy_mode": privacy.value,
            "db": str(db),
            "job_id": job.job_id,
        }
        _atomic_json(_task_record_path(runtime, args.project, args.task_id), record)
        result = _run_enabled(repository, job.job_id, TestProfile(worktree.test_profile))
        return _safe_payload(record, repository, result)
    finally:
        repository.close()


def command_status(args) -> dict:
    runtime = _runtime_root(args.runtime)
    record, repository, job = _open_task(runtime, args.project, args.task_id)
    try:
        return _safe_payload(record, repository, job)
    finally:
        repository.close()


def command_review_show(args) -> dict:
    runtime = _runtime_root(args.runtime)
    record, repository, job = _open_task(runtime, args.project, args.task_id)
    try:
        if job.current_stage is not WorkflowStage.REVIEW:
            raise ValueError("task is not at the Review stage")
        round_number = job.review_round + 1
        unit = repository.review_work_unit_for_round(job.job_id, job.owner_id, round_number)
        patch = repository.reconstruct_reviewer_patch(job.job_id, job.owner_id, round_number)
        result = {
            "project_id": record["project_id"],
            "task_id": record["task_id"],
            "job_id": job.job_id,
            "review_round": round_number,
            "review_work_unit_id": unit.review_work_unit_id,
            "patch_sha256": unit.patch_sha256,
            "candidate_identity_sha256": unit.candidate_identity_sha256,
        }
        recommendation = load_gemini_recommendation(repository, unit.review_work_unit_id)
        summary = _gemini_summary(recommendation)
        if summary is not None:
            result["gemini_advisory"] = summary
            if recommendation and recommendation.get("status") == "READY":
                result["gemini_findings"] = recommendation.get("findings", [])
        if args.output:
            raw = Path(args.output).expanduser()
            if raw.is_symlink():
                raise ValueError("review output must not be a symlink")
            output = raw.resolve()
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
    runtime = _runtime_root(args.runtime)
    record, repository, job = _open_task(runtime, args.project, args.task_id)
    try:
        if job.current_stage is not WorkflowStage.REVIEW:
            raise ValueError("task is not at the Review stage")
        round_number = job.review_round + 1
        unit = repository.review_work_unit_for_round(job.job_id, job.owner_id, round_number)
        submission = repository.submit_review_result(
            job.job_id,
            job.owner_id,
            round_number,
            unit.review_work_unit_id,
            ReviewResult("PASS"),
        )
        return {
            "project_id": record["project_id"],
            "task_id": record["task_id"],
            "review_status": submission.status,
            "result_hash": submission.result_hash,
        }
    finally:
        repository.close()


def command_review_fail(args) -> dict:
    runtime = _runtime_root(args.runtime)
    record, repository, job = _open_task(runtime, args.project, args.task_id)
    try:
        if job.current_stage is not WorkflowStage.REVIEW:
            raise ValueError("task is not at the Review stage")
        round_number = job.review_round + 1
        unit = repository.review_work_unit_for_round(job.job_id, job.owner_id, round_number)
        findings = _parse_findings(args.findings_file)
        submission = repository.submit_review_result(
            job.job_id,
            job.owner_id,
            round_number,
            unit.review_work_unit_id,
            ReviewResult("FAIL", findings),
        )
        return {
            "project_id": record["project_id"],
            "task_id": record["task_id"],
            "review_status": submission.status,
            "findings_count": len(findings),
            "result_hash": submission.result_hash,
        }
    finally:
        repository.close()


def command_continue(args) -> dict:
    runtime = _runtime_root(args.runtime)
    record, repository, job = _open_task(runtime, args.project, args.task_id)
    try:
        if (
            job.current_stage is WorkflowStage.REVIEW
            and job.status is JobStatus.WAITING
            and job.resume_state == "REVIEW_RESULT_PENDING"
        ):
            round_number = job.review_round + 1
            unit = repository.review_work_unit_for_round(job.job_id, job.owner_id, round_number)
            repository.submitted_review_result(
                job.job_id,
                job.owner_id,
                round_number,
                unit.review_work_unit_id,
            )
        result = _run_enabled(repository, job.job_id, TestProfile(record["test_profile"]))
        return _safe_payload(record, repository, result)
    finally:
        repository.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generic Local Qwen project operator")
    parser.add_argument("--runtime", default=str(DEFAULT_GENERIC_PROJECT_RUNTIME))
    parser.add_argument("--owner", default=os.environ.get("LOCAL_QWEN_OWNER_ID", "local-owner"))
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register")
    register.add_argument("--repo", required=True)
    register.add_argument("--project-id")
    register.set_defaults(handler=command_register)

    listing = sub.add_parser("list")
    listing.set_defaults(handler=command_list)

    task = sub.add_parser("task")
    task.add_argument("--project", required=True)
    task.add_argument("--task-id", required=True)
    task.add_argument("--prompt-file", required=True)
    task.add_argument("--title", default="GENERIC_LOCAL_QWEN_TASK")
    task.add_argument("--base-ref", default="HEAD")
    task.add_argument("--test-profile", choices=tuple(item.value for item in TestProfile))
    task.add_argument("--privacy", default=PrivacyMode.RESTRICTED.value, choices=tuple(item.value for item in PrivacyMode))
    task.add_argument("--risk", default="LOW", choices=("LOW", "MEDIUM", "HIGH"))
    task.add_argument("--timeout", type=float, default=900)
    task.set_defaults(handler=command_task)

    status = sub.add_parser("status")
    status.add_argument("--project", required=True)
    status.add_argument("--task-id", required=True)
    status.set_defaults(handler=command_status)

    show = sub.add_parser("review-show")
    show.add_argument("--project", required=True)
    show.add_argument("--task-id", required=True)
    show.add_argument("--output")
    show.set_defaults(handler=command_review_show)

    approve = sub.add_parser("review-pass")
    approve.add_argument("--project", required=True)
    approve.add_argument("--task-id", required=True)
    approve.set_defaults(handler=command_review_pass)

    reject = sub.add_parser("review-fail")
    reject.add_argument("--project", required=True)
    reject.add_argument("--task-id", required=True)
    reject.add_argument("--findings-file", required=True)
    reject.set_defaults(handler=command_review_fail)

    resume = sub.add_parser("continue")
    resume.add_argument("--project", required=True)
    resume.add_argument("--task-id", required=True)
    resume.set_defaults(handler=command_continue)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
    except Exception as error:
        print(json.dumps({
            "status": "ERROR",
            "error": type(error).__name__,
            "message": str(error)[:1000],
        }, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
