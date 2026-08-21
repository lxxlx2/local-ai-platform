from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol

from local_ai_control.services.security import SecretFirewall

AI_ROOT = Path("/Users/jerson/AI")
CONTROL_PLANE_ROOT = AI_ROOT / "control-plane"
CONTROL_PLANE_PYTHON = AI_ROOT / "runtime/control-plane-venv/bin/python"
SUPERVISOR_RUNTIME = AI_ROOT / "runtime/supervisor"
SUPERVISOR_DB = SUPERVISOR_RUNTIME / "supervisor.db"
MAX_ACTIVE_JOBS = 1
MAX_SUMMARY_CHARS = 4096
MAX_EVENT_PAYLOAD_CHARS = 2048
MAX_EVENTS_PER_JOB = 5000
MAX_TERMINAL_JOBS = 500
MAX_FINDINGS_PER_REVIEW = 100
MAX_FINDINGS_PER_JOB = 500
MAX_WORK_UNIT_PROMPT_BYTES = 256_000
MAX_CONTENT_FILES = 2_000
LOCK_TTL_SECONDS = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_private_directory(path: Path) -> Path:
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(0o700)
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError as error:
        raise PermissionError(f"unable to enforce owner-only directory permissions: {target}") from error
    if mode & 0o077:
        raise PermissionError(f"owner-only directory permission policy failed: {target}")
    return target


def ensure_private_file(path: Path) -> Path:
    target = Path(path)
    if not target.exists():
        return target
    try:
        target.chmod(0o600)
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError as error:
        raise PermissionError(f"unable to enforce owner-only file permissions: {target}") from error
    if mode & 0o077:
        raise PermissionError(f"owner-only file permission policy failed: {target}")
    return target


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"


class WorkflowStage(str, Enum):
    INTAKE = "INTAKE"
    PRODUCER = "PRODUCER"
    VALIDATION = "VALIDATION"
    SELF_ACCEPTANCE = "SELF_ACCEPTANCE"
    REVIEW = "REVIEW"
    REVISION = "REVISION"
    SECURITY = "SECURITY"
    GIT_GATE = "GIT_GATE"
    DONE = "DONE"


class StageResultStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WAIT = "WAIT"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"


class LeaseLostError(RuntimeError):
    """Raised when the process no longer owns the singleton lease."""


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    file: str
    evidence: str
    recommended_fix: str


@dataclass(frozen=True)
class PersistedReviewFinding:
    finding_id: str
    job_id: str
    review_round: int
    severity: str
    file: str
    evidence: str
    recommended_fix: str
    created_at: str
    integrity_hash: str
    status: str
    consumed_by_revision: str | None


@dataclass(frozen=True)
class WorkflowJob:
    job_id: str
    title: str
    project_scope: str
    created_at: str
    updated_at: str
    owner_id: str
    risk_level: str
    status: JobStatus
    current_stage: WorkflowStage
    attempt: int
    review_round: int
    max_review_rounds: int
    max_attempts_per_stage: int
    last_error: str | None
    resume_state: str | None
    created_by: str
    metadata: dict
    next_retry_at: float | None


@dataclass(frozen=True)
class StageContext:
    job: WorkflowJob
    stage: WorkflowStage
    attempt: int
    idempotency_key: str
    timeout_seconds: float
    repository: "SupervisorRepository"

    def current_review_findings(self) -> tuple[PersistedReviewFinding, ...]:
        if self.job.review_round <= 0:
            return ()
        return tuple(self.repository.review_findings(self.job.job_id, self.job.owner_id, self.job.review_round))


@dataclass(frozen=True)
class StageResult:
    status: StageResultStatus
    summary: str
    artifacts: tuple[dict, ...] = ()
    error: str | None = None
    metrics: dict = field(default_factory=dict)
    next_hint: str | None = None
    review_findings: tuple[ReviewFinding, ...] = ()

    @classmethod
    def passed(cls, summary: str, **kwargs) -> "StageResult":
        return cls(StageResultStatus.PASS, summary, **kwargs)

    @classmethod
    def failed(cls, summary: str, error: str | None = None, **kwargs) -> "StageResult":
        return cls(StageResultStatus.FAIL, summary, error=error, **kwargs)


class StageRunner(Protocol):
    def run(self, context: StageContext) -> StageResult: ...


def _bounded(value: str | None, limit: int = MAX_SUMMARY_CHARS) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: max(0, limit - 15)] + "…[TRUNCATED]"


def _json_exact(value, limit: int) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    if len(encoded.encode()) > limit:
        raise ValueError("structured payload exceeds safe persistence bound")
    return encoded


def _safe_json(value, limit=MAX_EVENT_PAYLOAD_CHARS) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) > limit:
        encoded = json.dumps({"truncated": True, "sha256": hashlib.sha256(encoded.encode()).hexdigest()})
    return encoded


def _safe_text(value: str | None, limit: int = MAX_SUMMARY_CHARS) -> str | None:
    bounded = _bounded(value, limit)
    if bounded and SecretFirewall().inspect(bounded).action == "BLOCK":
        return "[REDACTED_BY_SECRET_FIREWALL]"
    return bounded


def _safe_audit_value(value):
    if isinstance(value, Mapping):
        return {str(key): _safe_audit_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_audit_value(item) for item in value]
    if isinstance(value, str) and SecretFirewall().inspect(value).action == "BLOCK":
        return {"redacted": True, "sha256": hashlib.sha256(value.encode()).hexdigest()}
    return value


def _safe_metadata(metadata: Mapping | None) -> dict:
    clean = dict(metadata or {})
    for key in list(clean):
        if re.search(r"prompt|token|secret|password|credential|cookie|authorization", str(key), re.I):
            value = str(clean.pop(key))
            clean[f"{key}_sha256"] = hashlib.sha256(value.encode()).hexdigest()
    clean = _safe_audit_value(clean)
    return json.loads(_safe_json(clean, 16_000))


def _normalize_relative_path(value: str, root: Path, label: str) -> str:
    if not value:
        return ""
    raw = Path(value)
    candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise PermissionError(f"{label} path traversal denied")
    return candidate.relative_to(root.resolve()).as_posix()


def _safe_review_text(value: str) -> str:
    if SecretFirewall().inspect(value).action == "BLOCK":
        return "[REDACTED_BY_SECRET_FIREWALL]"
    return _bounded(value, MAX_SUMMARY_CHARS) or ""


@dataclass(frozen=True)
class ReviewResult:
    status: str
    findings: tuple[ReviewFinding, ...] = ()

    def to_stage_result(self, repo_root: Path = AI_ROOT) -> StageResult:
        if self.status not in {"PASS", "FAIL"}:
            raise ValueError("review status must be PASS or FAIL")
        if self.status == "PASS" and self.findings:
            raise ValueError("PASS review cannot contain findings")
        normalized = []
        root = Path(repo_root).resolve()
        for finding in self.findings:
            if finding.severity not in {"BLOCKING", "HIGH", "MEDIUM", "LOW"}:
                raise ValueError("invalid review severity")
            path = _normalize_relative_path(finding.file, root, "review finding")
            normalized.append({
                "severity": finding.severity,
                "file": path,
                "evidence_sha256": hashlib.sha256(finding.evidence.encode()).hexdigest(),
                "recommended_fix_sha256": hashlib.sha256(finding.recommended_fix.encode()).hexdigest(),
            })
        digest = hashlib.sha256(_json_exact(normalized, 64_000).encode()).hexdigest()
        metrics = {
            "findings_count": len(normalized),
            "blocking_findings": sum(item["severity"] == "BLOCKING" for item in normalized),
        }
        artifact = ({"kind": "review_metadata", "reference": f"review:{digest}", "size_bytes": 0},)
        if self.status == "PASS":
            return StageResult.passed("Independent review contract returned PASS", metrics=metrics, artifacts=artifact)
        return StageResult.failed(
            "Independent review contract returned FAIL",
            metrics=metrics,
            artifacts=artifact,
            review_findings=self.findings,
        )


TERMINAL_JOB_STATUSES = {JobStatus.FAILED, JobStatus.CANCELED, JobStatus.COMPLETED}
SAFE_RECOVERY_STAGES = {
    WorkflowStage.INTAKE,
    WorkflowStage.VALIDATION,
    WorkflowStage.SELF_ACCEPTANCE,
    WorkflowStage.REVIEW,
    WorkflowStage.SECURITY,
}
NEXT_STAGE = {
    WorkflowStage.INTAKE: WorkflowStage.PRODUCER,
    WorkflowStage.PRODUCER: WorkflowStage.VALIDATION,
    WorkflowStage.VALIDATION: WorkflowStage.SELF_ACCEPTANCE,
    WorkflowStage.SELF_ACCEPTANCE: WorkflowStage.REVIEW,
    WorkflowStage.REVISION: WorkflowStage.VALIDATION,
    WorkflowStage.REVIEW: WorkflowStage.SECURITY,
    WorkflowStage.SECURITY: WorkflowStage.GIT_GATE,
    WorkflowStage.GIT_GATE: WorkflowStage.DONE,
}


@dataclass(frozen=True)
class CodexTaskSpec:
    repo_root: Path
    allowed_paths: tuple[Path, ...]
    task_prompt: str
    risk_level: str
    timeout_seconds: float
    model_role: str
    expected_output_schema: dict

    def validate(self) -> dict:
        root = self.repo_root.resolve()
        if root != AI_ROOT.resolve():
            raise PermissionError("Codex repo_root denied")
        allowed = []
        for path in self.allowed_paths:
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise PermissionError("Codex allowed_path traversal denied")
            allowed.append(str(resolved))
        if not self.task_prompt or len(self.task_prompt.encode()) > MAX_WORK_UNIT_PROMPT_BYTES:
            raise ValueError("Codex task prompt outside safe size bound")
        if SecretFirewall().inspect(self.task_prompt).action == "BLOCK":
            raise ValueError("Codex task prompt rejected by Secret Firewall")
        if not 1 <= float(self.timeout_seconds) <= 3600:
            raise ValueError("Codex timeout outside safe range")
        schema = _safe_audit_value(self.expected_output_schema)
        _json_exact(schema, 16_000)
        return {
            "repo_root": str(root),
            "allowed_paths": allowed,
            "task_prompt_sha256": hashlib.sha256(self.task_prompt.encode()).hexdigest(),
            "risk_level": self.risk_level,
            "timeout_seconds": float(self.timeout_seconds),
            "model_role": self.model_role,
            "expected_output_schema": schema,
        }


@dataclass(frozen=True)
class WorkUnitSpec:
    work_unit_id: str
    job_id: str
    stage: WorkflowStage
    repo_root: Path
    allowed_paths: tuple[Path, ...]
    risk_level: str
    timeout_seconds: float
    model_role: str
    expected_output_schema: dict
    prompt_content_ref: str
    prompt_sha256: str
    created_at: str
    status: str
    review_round: int


class OwnerPrivateContentStore:
    def __init__(self, root: Path):
        self.root = ensure_private_directory(root)

    def _path(self, content_ref: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,96}\.prompt", content_ref):
            raise PermissionError("invalid private content reference")
        candidate = (self.root / content_ref).resolve()
        if candidate.parent != self.root.resolve():
            raise PermissionError("private content path traversal denied")
        return candidate

    def put(self, work_unit_id: str, prompt: str) -> tuple[str, str]:
        encoded = prompt.encode()
        if not encoded or len(encoded) > MAX_WORK_UNIT_PROMPT_BYTES:
            raise ValueError("work unit prompt outside safe size bound")
        if SecretFirewall().inspect(prompt).action == "BLOCK":
            raise ValueError("work unit prompt rejected by Secret Firewall")
        content_ref = f"{work_unit_id}.prompt"
        path = self._path(content_ref)
        digest = hashlib.sha256(encoded).hexdigest()
        if path.exists():
            current = path.read_bytes()
            if hashlib.sha256(current).hexdigest() != digest or current != encoded:
                raise ValueError("work unit content id conflicts with existing content")
            ensure_private_file(path)
            return content_ref, digest
        if sum(1 for _ in self.root.glob("*.prompt")) >= MAX_CONTENT_FILES:
            raise RuntimeError("private content store capacity reached")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            ensure_private_file(path)
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return content_ref, digest

    def get(self, content_ref: str, expected_sha256: str) -> str:
        path = self._path(content_ref)
        ensure_private_file(path)
        data = path.read_bytes()
        if len(data) > MAX_WORK_UNIT_PROMPT_BYTES:
            raise ValueError("private content exceeds safe size bound")
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise ValueError("private content integrity mismatch")
        return data.decode("utf-8")

    def delete(self, content_ref: str) -> None:
        self._path(content_ref).unlink(missing_ok=True)
