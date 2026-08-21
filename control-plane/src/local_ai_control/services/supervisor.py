from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

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
LOCK_TTL_SECONDS = 30


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    file: str
    evidence: str
    recommended_fix: str


@dataclass(frozen=True)
class ReviewResult:
    status: str
    findings: tuple[ReviewFinding, ...] = ()

    def to_stage_result(self, repo_root: Path = AI_ROOT) -> "StageResult":
        if self.status not in {"PASS", "FAIL"}:
            raise ValueError("review status must be PASS or FAIL")
        normalized = []
        for finding in self.findings:
            if finding.severity not in {"BLOCKING", "HIGH", "MEDIUM", "LOW"}:
                raise ValueError("invalid review severity")
            candidate = (repo_root / finding.file).resolve()
            if finding.file and not candidate.is_relative_to(repo_root.resolve()):
                raise PermissionError("review finding path traversal denied")
            normalized.append({
                "severity": finding.severity,
                "file": finding.file,
                "evidence_sha256": hashlib.sha256(finding.evidence.encode()).hexdigest(),
                "recommended_fix_sha256": hashlib.sha256(finding.recommended_fix.encode()).hexdigest(),
            })
        digest = hashlib.sha256(_safe_json(normalized, 64_000).encode()).hexdigest()
        metrics = {
            "findings_count": len(normalized),
            "blocking_findings": sum(item["severity"] == "BLOCKING" for item in normalized),
        }
        artifact = ({"kind": "review_metadata", "reference": f"review:{digest}", "size_bytes": 0},)
        if self.status == "PASS":
            return StageResult.passed("Independent review contract returned PASS", metrics=metrics, artifacts=artifact)
        return StageResult.failed("Independent review contract returned FAIL", metrics=metrics, artifacts=artifact)


TERMINAL_JOB_STATUSES = {
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.COMPLETED,
}

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


@dataclass(frozen=True)
class StageResult:
    status: StageResultStatus
    summary: str
    artifacts: tuple[dict, ...] = ()
    error: str | None = None
    metrics: dict = field(default_factory=dict)
    next_hint: str | None = None

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
    return value if len(value) <= limit else value[: limit - 15] + "…[TRUNCATED]"


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
    encoded = _safe_json(clean, 16_000)
    return json.loads(encoded)


class SupervisorRepository:
    """Owner-private durable state for the program-level workflow supervisor."""

    def __init__(self, path: Path = SUPERVISOR_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")

    def migrate(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS supervisor_jobs(
              job_id TEXT PRIMARY KEY, title TEXT NOT NULL, project_scope TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, owner_id TEXT NOT NULL,
              risk_level TEXT NOT NULL, status TEXT NOT NULL, current_stage TEXT NOT NULL,
              attempt INTEGER NOT NULL DEFAULT 0, review_round INTEGER NOT NULL DEFAULT 0,
              max_review_rounds INTEGER NOT NULL, max_attempts_per_stage INTEGER NOT NULL,
              last_error TEXT, resume_state TEXT, created_by TEXT NOT NULL,
              metadata_json TEXT NOT NULL, next_retry_at REAL
            );
            CREATE INDEX IF NOT EXISTS supervisor_jobs_queue_idx
              ON supervisor_jobs(status, next_retry_at, created_at);
            CREATE TABLE IF NOT EXISTS supervisor_stage_runs(
              run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
              attempt INTEGER NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
              completed_at TEXT, idempotency_key TEXT NOT NULL UNIQUE,
              summary TEXT, error TEXT, metrics_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(job_id) REFERENCES supervisor_jobs(job_id)
            );
            CREATE INDEX IF NOT EXISTS supervisor_stage_runs_job_idx
              ON supervisor_stage_runs(job_id, stage, attempt);
            CREATE TABLE IF NOT EXISTS supervisor_events(
              event_id TEXT PRIMARY KEY, job_id TEXT, event_type TEXT NOT NULL,
              stage TEXT, created_at TEXT NOT NULL, payload_json TEXT NOT NULL,
              dedupe_key TEXT UNIQUE
            );
            CREATE INDEX IF NOT EXISTS supervisor_events_job_idx
              ON supervisor_events(job_id, created_at);
            CREATE TABLE IF NOT EXISTS supervisor_artifacts(
              artifact_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
              kind TEXT NOT NULL, reference TEXT NOT NULL, size_bytes INTEGER,
              sha256 TEXT, created_at TEXT NOT NULL,
              FOREIGN KEY(job_id) REFERENCES supervisor_jobs(job_id)
            );
            CREATE TABLE IF NOT EXISTS supervisor_locks(
              lock_name TEXT PRIMARY KEY, owner_token TEXT NOT NULL, pid INTEGER NOT NULL,
              acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL, expires_at REAL NOT NULL
            );
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _job_from_row(self, row) -> WorkflowJob:
        return WorkflowJob(
            job_id=row["job_id"], title=row["title"], project_scope=row["project_scope"],
            created_at=row["created_at"], updated_at=row["updated_at"], owner_id=row["owner_id"],
            risk_level=row["risk_level"], status=JobStatus(row["status"]),
            current_stage=WorkflowStage(row["current_stage"]), attempt=row["attempt"],
            review_round=row["review_round"], max_review_rounds=row["max_review_rounds"],
            max_attempts_per_stage=row["max_attempts_per_stage"], last_error=row["last_error"],
            resume_state=row["resume_state"], created_by=row["created_by"],
            metadata=json.loads(row["metadata_json"]), next_retry_at=row["next_retry_at"],
        )

    def create_job(
        self, title: str, owner_id: str, project_scope: str = str(AI_ROOT),
        risk_level: str = "LOW", created_by: str = "owner",
        metadata: Mapping | None = None, max_review_rounds: int = 2,
        max_attempts_per_stage: int = 2, job_id: str | None = None,
    ) -> WorkflowJob:
        resolved = Path(project_scope).resolve()
        if resolved != AI_ROOT:
            raise PermissionError("project_scope must be /Users/jerson/AI in V0.1")
        if SecretFirewall().inspect(title).action == "BLOCK":
            raise ValueError("job title rejected by Secret Firewall")
        if not 1 <= max_review_rounds <= 5 or not 1 <= max_attempts_per_stage <= 5:
            raise ValueError("round/attempt limit outside safe range")
        now, identifier = utc_now(), job_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,46}", identifier):
            raise ValueError("job_id is not callback-safe")
        if job_id:
            existing = self.db.execute("SELECT * FROM supervisor_jobs WHERE job_id=?", (job_id,)).fetchone()
            if existing:
                job = self._job_from_row(existing)
                if job.owner_id != str(owner_id) or job.title != title:
                    raise ValueError("idempotency key conflicts with existing job")
                return job
        with self.db:
            self.db.execute(
                """INSERT INTO supervisor_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, _bounded(title, 200), str(resolved), now, now, str(owner_id), risk_level,
                 JobStatus.QUEUED.value, WorkflowStage.INTAKE.value, 0, 0, max_review_rounds,
                 max_attempts_per_stage, None, None, created_by, _safe_json(_safe_metadata(metadata), 16_000), None),
            )
            self.record_event(identifier, "JOB_CREATED", WorkflowStage.INTAKE, {"risk_level": risk_level}, commit=False)
        return self.get_job(identifier)

    def get_job(self, job_id: str) -> WorkflowJob:
        row = self.db.execute("SELECT * FROM supervisor_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError("job not found")
        return self._job_from_row(row)

    def get_job_for_owner(self, job_id: str, owner_id: str) -> WorkflowJob:
        job = self.get_job(job_id)
        if job.owner_id != str(owner_id):
            raise PermissionError("supervisor job owner mismatch")
        return job

    def list_jobs(self, owner_id: str | None = None, limit: int = 50) -> list[WorkflowJob]:
        sql, values = "SELECT * FROM supervisor_jobs", []
        if owner_id is not None:
            sql += " WHERE owner_id=?"; values.append(str(owner_id))
        sql += " ORDER BY created_at DESC LIMIT ?"; values.append(min(max(limit, 1), 200))
        return [self._job_from_row(row) for row in self.db.execute(sql, values).fetchall()]

    def update_job(self, job_id: str, **changes) -> WorkflowJob:
        allowed = {
            "status", "current_stage", "attempt", "review_round", "last_error",
            "resume_state", "next_retry_at", "metadata_json",
        }
        if not changes or set(changes) - allowed:
            raise ValueError("invalid job update")
        values = {key: (value.value if isinstance(value, Enum) else value) for key, value in changes.items()}
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.db:
            self.db.execute(f"UPDATE supervisor_jobs SET {assignments} WHERE job_id=?", (*values.values(), job_id))
        return self.get_job(job_id)

    def record_event(self, job_id: str | None, event_type: str, stage: WorkflowStage | None = None,
                     payload: Mapping | None = None, dedupe_key: str | None = None, commit=True) -> bool:
        try:
            self.db.execute(
                "INSERT INTO supervisor_events VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), job_id, event_type, stage.value if stage else None, utc_now(),
                 _safe_json(_safe_audit_value(payload or {})), dedupe_key),
            )
            if job_id:
                self.db.execute(
                    """DELETE FROM supervisor_events WHERE event_id IN (
                       SELECT event_id FROM supervisor_events WHERE job_id=? ORDER BY created_at DESC LIMIT -1 OFFSET ?
                    )""", (job_id, MAX_EVENTS_PER_JOB),
                )
            if commit:
                self.db.commit()
            return True
        except sqlite3.IntegrityError:
            if commit:
                self.db.rollback()
            return False

    def events(self, job_id: str, limit=100) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM supervisor_events WHERE job_id=? ORDER BY created_at LIMIT ?",
            (job_id, min(max(limit, 1), 1000)),
        ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def stage_attempts(self, job_id: str, stage: WorkflowStage) -> int:
        row = self.db.execute(
            "SELECT COALESCE(MAX(attempt),0) AS attempts FROM supervisor_stage_runs WHERE job_id=? AND stage=?",
            (job_id, stage.value),
        ).fetchone()
        return int(row["attempts"])

    def begin_stage(self, job: WorkflowJob) -> tuple[str, int, str] | None:
        attempt = self.stage_attempts(job.job_id, job.current_stage) + 1
        key = f"{job.job_id}:{job.current_stage.value}:{attempt}"
        run_id = str(uuid.uuid4())
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO supervisor_stage_runs(run_id,job_id,stage,attempt,status,started_at,idempotency_key) VALUES(?,?,?,?,?,?,?)",
                    (run_id, job.job_id, job.current_stage.value, attempt, "RUNNING", utc_now(), key),
                )
                self.db.execute(
                    "UPDATE supervisor_jobs SET status=?,attempt=?,updated_at=?,last_error=NULL WHERE job_id=?",
                    (JobStatus.RUNNING.value, attempt, utc_now(), job.job_id),
                )
                self.record_event(job.job_id, "STAGE_STARTED", job.current_stage, {"attempt": attempt}, f"started:{key}", commit=False)
            return run_id, attempt, key
        except sqlite3.IntegrityError:
            return None

    def finish_stage(self, run_id: str, job_id: str, stage: WorkflowStage, result: StageResult) -> None:
        with self.db:
            self.db.execute(
                "UPDATE supervisor_stage_runs SET status=?,completed_at=?,summary=?,error=?,metrics_json=? WHERE run_id=?",
                (result.status.value, utc_now(), _safe_text(result.summary), _safe_text(result.error),
                 _safe_json(_safe_audit_value(result.metrics), 8000), run_id),
            )
            for artifact in result.artifacts:
                self.db.execute(
                    "INSERT INTO supervisor_artifacts VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), job_id, stage.value, str(artifact.get("kind", "metadata")),
                     _safe_text(str(artifact.get("reference", "")), 500), artifact.get("size_bytes"),
                     artifact.get("sha256"), utc_now()),
                )

    def prune_terminal_jobs(self, keep: int = MAX_TERMINAL_JOBS) -> int:
        keep = min(max(int(keep), 1), MAX_TERMINAL_JOBS)
        rows = self.db.execute(
            """SELECT job_id FROM supervisor_jobs WHERE status IN (?,?,?,?)
               ORDER BY updated_at DESC LIMIT -1 OFFSET ?""",
            (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELED.value,
             JobStatus.BLOCKED.value, keep),
        ).fetchall()
        identifiers = [row["job_id"] for row in rows]
        with self.db:
            for job_id in identifiers:
                self.db.execute("DELETE FROM supervisor_artifacts WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_stage_runs WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_events WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_jobs WHERE job_id=?", (job_id,))
        return len(identifiers)

    def latest_stage_runs(self, job_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM supervisor_stage_runs WHERE job_id=? ORDER BY started_at", (job_id,)
        ).fetchall()]

    def acquire_lock(self, owner_token: str, pid: int, ttl=LOCK_TTL_SECONDS) -> bool:
        now = time.time()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM supervisor_locks WHERE lock_name='consumer'").fetchone()
            if row and row["owner_token"] != owner_token and row["expires_at"] > now:
                self.db.rollback(); return False
            self.db.execute(
                "INSERT OR REPLACE INTO supervisor_locks VALUES('consumer',?,?,?,?,?)",
                (owner_token, pid, now, now, now + ttl),
            )
            self.db.commit(); return True
        except sqlite3.Error:
            self.db.rollback(); return False

    def heartbeat_lock(self, owner_token: str, ttl=LOCK_TTL_SECONDS) -> bool:
        now = time.time()
        with self.db:
            cursor = self.db.execute(
                "UPDATE supervisor_locks SET heartbeat_at=?,expires_at=? WHERE lock_name='consumer' AND owner_token=?",
                (now, now + ttl, owner_token),
            )
        return cursor.rowcount == 1

    def release_lock(self, owner_token: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM supervisor_locks WHERE lock_name='consumer' AND owner_token=?", (owner_token,))

    def lock_snapshot(self) -> dict | None:
        row = self.db.execute("SELECT * FROM supervisor_locks WHERE lock_name='consumer'").fetchone()
        return dict(row) if row else None

    def queued_job(self) -> WorkflowJob | None:
        now = time.time()
        row = self.db.execute(
            """SELECT * FROM supervisor_jobs
               WHERE status=? OR (status=? AND resume_state='RETRY_SCHEDULED' AND COALESCE(next_retry_at,0)<=?)
               ORDER BY created_at LIMIT 1""",
            (JobStatus.QUEUED.value, JobStatus.WAITING.value, now),
        ).fetchone()
        return self._job_from_row(row) if row else None

    def counts(self) -> dict:
        rows = self.db.execute("SELECT status,COUNT(*) AS count FROM supervisor_jobs GROUP BY status").fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        return {
            "active": counts.get(JobStatus.RUNNING.value, 0),
            "queued": counts.get(JobStatus.QUEUED.value, 0),
            "waiting": counts.get(JobStatus.WAITING.value, 0),
            "failed": counts.get(JobStatus.FAILED.value, 0) + counts.get(JobStatus.BLOCKED.value, 0),
        }

    def health_snapshot(self) -> dict:
        self.db.execute("SELECT 1").fetchone()
        counts = self.counts()
        last = self.db.execute(
            "SELECT job_id,title,updated_at FROM supervisor_jobs WHERE status=? ORDER BY updated_at DESC LIMIT 1",
            (JobStatus.COMPLETED.value,),
        ).fetchone()
        error = self.db.execute(
            "SELECT last_error FROM supervisor_jobs WHERE last_error IS NOT NULL ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        lock = self.lock_snapshot()
        return {
            "status": "HEALTHY", "db_reachable": True,
            "single_instance_lock": bool(lock and lock["expires_at"] > time.time()),
            "active_jobs": counts["active"], "queue_depth": counts["queued"],
            "last_completed_job": dict(last) if last else None,
            "last_error": error["last_error"] if error else None,
        }


class StaticPassRunner:
    def __init__(self, summary: str):
        self.summary = summary

    def run(self, context: StageContext) -> StageResult:
        return StageResult.passed(self.summary, metrics={"attempt": context.attempt})


class MockReviewRunner:
    """Deterministic demo contract: first review fails, second review passes."""

    def run(self, context: StageContext) -> StageResult:
        if not context.job.metadata.get("supervisor_demo"):
            return StageResult(StageResultStatus.BLOCKED, "Real reviewer not configured in V0.1", error="REVIEWER_NOT_CONFIGURED")
        if context.job.review_round == 0:
            return ReviewResult("FAIL", (ReviewFinding(
                "BLOCKING", "control-plane/tests/test_workflow_supervisor.py",
                "synthetic demo evidence", "synthetic demo revision",
            ),)).to_stage_result()
        return ReviewResult("PASS").to_stage_result()


class MockCodexRunner:
    def run(self, context: StageContext) -> StageResult:
        if not context.job.metadata.get("supervisor_demo"):
            return StageResult(StageResultStatus.BLOCKED, "Mock Codex runner is demo-only", error="DEMO_ONLY")
        return StageResult.passed(f"Mock {context.stage.value.lower()} completed", metrics={"mock": True})


@dataclass(frozen=True)
class SafeCommandPolicy:
    python: Path = CONTROL_PLANE_PYTHON
    cwd_root: Path = CONTROL_PLANE_ROOT

    def validate(self, argv: Sequence[str], cwd: Path) -> tuple[str, ...]:
        command = tuple(str(item) for item in argv)
        resolved_cwd = Path(cwd).resolve()
        if not resolved_cwd.is_relative_to(self.cwd_root.resolve()):
            raise PermissionError("validation cwd outside control-plane")
        if len(command) < 3 or command[:3] != (str(self.python), "-m", "pytest"):
            raise PermissionError("command is not allowlisted pytest argv")
        for item in command[3:]:
            if item == "-q" or re.fullmatch(r"--maxfail=[1-9][0-9]*", item):
                continue
            candidate = (resolved_cwd / item.split("::", 1)[0]).resolve()
            if not candidate.is_relative_to((self.cwd_root / "tests").resolve()):
                raise PermissionError("pytest target outside tests directory")
        return command


class LocalValidationRunner:
    def __init__(self, argv: Sequence[str] | None = None, cwd: Path = CONTROL_PLANE_ROOT,
                 timeout_seconds: float = 120, policy: SafeCommandPolicy | None = None):
        self.argv = tuple(argv or (str(CONTROL_PLANE_PYTHON), "-m", "pytest", "-q"))
        self.cwd = Path(cwd)
        self.timeout_seconds = timeout_seconds
        self.policy = policy or SafeCommandPolicy()

    @staticmethod
    def _summary(stdout: str, stderr: str) -> str:
        combined = (stdout + "\n" + stderr).strip()
        if SecretFirewall().inspect(combined).action == "BLOCK":
            return "validation output redacted by Secret Firewall"
        return _bounded(combined, MAX_SUMMARY_CHARS) or "validation produced no output"

    def run(self, context: StageContext) -> StageResult:
        try:
            command = self.policy.validate(self.argv, self.cwd)
        except (PermissionError, ValueError) as error:
            return StageResult(StageResultStatus.BLOCKED, "Validation command denied", error=str(error))
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, cwd=self.cwd, capture_output=True, text=True, shell=False,
                timeout=min(self.timeout_seconds, context.timeout_seconds), check=False,
                env={"PATH": os.defpath, "PYTHONPATH": str(CONTROL_PLANE_ROOT / "src"), "PYTHONHASHSEED": "0"},
            )
        except subprocess.TimeoutExpired:
            return StageResult(StageResultStatus.TIMEOUT, "Validation timed out", error="TIMEOUT",
                               metrics={"duration_seconds": round(time.monotonic() - started, 3)})
        duration = round(time.monotonic() - started, 3)
        summary = self._summary(completed.stdout, completed.stderr)
        metrics = {"return_code": completed.returncode, "duration_seconds": duration,
                   "stdout_chars": len(completed.stdout), "stderr_chars": len(completed.stderr)}
        if completed.returncode == 0:
            return StageResult.passed(summary, metrics=metrics)
        return StageResult.failed(summary, error=f"pytest_exit_{completed.returncode}", metrics=metrics)


class SecurityRunner:
    """Reuses SecretFirewall and the repository's tracked-runtime policy."""

    forbidden_tracked = re.compile(
        r"(^|/)(?:runtime|models|cache|tmp|inbox|output|logs)(/|$)|(?:\.sqlite3?|\.db|\.log|\.env|\.incomplete)$"
    )

    def __init__(self, repo_root: Path = AI_ROOT):
        self.repo_root = Path(repo_root).resolve()

    def _run_isolation_regression(self, context: StageContext) -> StageResult:
        return LocalValidationRunner(
            (
                str(CONTROL_PLANE_PYTHON), "-m", "pytest", "-q",
                "tests/test_gateway_v02.py", "tests/test_control.py",
            ),
            timeout_seconds=60,
        ).run(context)

    def run(self, context: StageContext) -> StageResult:
        if self.repo_root != AI_ROOT:
            return StageResult(StageResultStatus.BLOCKED, "Security scope denied", error="PATH_SCOPE")
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=self.repo_root, capture_output=True, text=True,
            shell=False, timeout=10, check=False,
        )
        if tracked.returncode != 0:
            return StageResult.failed("Unable to enumerate tracked files", error="GIT_LS_FILES")
        forbidden = [line for line in tracked.stdout.splitlines() if self.forbidden_tracked.search(line)]
        if forbidden:
            return StageResult.failed("Tracked runtime/secret policy failed", error="FORBIDDEN_TRACKED_FILE",
                                      metrics={"forbidden_count": len(forbidden)})
        changed = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"], cwd=self.repo_root,
            capture_output=True, text=True, shell=False, timeout=10, check=False,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=self.repo_root,
            capture_output=True, text=True, shell=False, timeout=10, check=False,
        )
        if changed.returncode != 0 or untracked.returncode != 0:
            return StageResult.failed("Unable to enumerate candidate files", error="GIT_CANDIDATE_FILES")
        firewall, scanned = SecretFirewall(), 0
        candidates = sorted(set(changed.stdout.splitlines()) | set(untracked.stdout.splitlines()))
        for relative in candidates:
            path = (self.repo_root / relative).resolve()
            if not path.is_relative_to(self.repo_root) or not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            scanned += 1
            if firewall.inspect(text).action == "BLOCK":
                return StageResult.failed("Credential scan failed", error="SECRET_SCAN",
                                          metrics={"files_scanned": scanned})
        isolation = self._run_isolation_regression(context)
        if isolation.status is not StageResultStatus.PASS:
            return StageResult.failed(
                "Security isolation regression failed",
                error="SECURITY_REGRESSION",
                metrics={"files_scanned": scanned, "isolation_return_code": isolation.metrics.get("return_code")},
            )
        return StageResult.passed(
            "Security policies and isolation regressions passed",
            metrics={"files_scanned": scanned, "forbidden_count": 0,
                     "isolation_return_code": isolation.metrics.get("return_code")},
        )


class GitGateRunner:
    """Read-only production gate. It never commits, pushes, or merges."""

    def __init__(self, repo_root: Path = AI_ROOT):
        self.repo_root = Path(repo_root).resolve()

    def run(self, context: StageContext) -> StageResult:
        completed = {
            row["stage"] for row in context.repository.db.execute(
                "SELECT stage FROM supervisor_stage_runs WHERE job_id=? AND status='PASS'",
                (context.job.job_id,),
            ).fetchall()
        }
        required = {
            WorkflowStage.VALIDATION.value,
            WorkflowStage.REVIEW.value,
            WorkflowStage.SECURITY.value,
        }
        missing = sorted(required - completed)
        if missing:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Git Gate prerequisites are incomplete",
                error="GIT_GATE_PREREQUISITES",
                metrics={"missing_stages": missing},
            )
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=self.repo_root,
            capture_output=True, text=True, shell=False, timeout=5, check=False,
        ).stdout.strip()
        if not branch or branch == "main":
            return StageResult(StageResultStatus.BLOCKED, "Git Gate requires a feature branch", error="MAIN_BRANCH_DENIED")
        return StageResult.passed(
            "Git Gate policy satisfied; V0.1 performs no Git mutation",
            metrics={"branch": branch, "git_mutation": False, "review_pending": True},
        )


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
        if root != AI_ROOT:
            raise PermissionError("Codex repo_root denied")
        for path in self.allowed_paths:
            if not path.resolve().is_relative_to(root):
                raise PermissionError("Codex allowed_path traversal denied")
        if SecretFirewall().inspect(self.task_prompt).action == "BLOCK":
            raise ValueError("Codex task prompt rejected by Secret Firewall")
        return {
            "repo_root": str(root), "allowed_paths": [str(path.resolve()) for path in self.allowed_paths],
            "task_prompt_sha256": hashlib.sha256(self.task_prompt.encode()).hexdigest(),
            "risk_level": self.risk_level, "timeout_seconds": self.timeout_seconds,
            "model_role": self.model_role, "expected_output_schema": _safe_audit_value(self.expected_output_schema),
        }


@dataclass(frozen=True)
class CodexCapability:
    status: str
    executable: str | None
    version: str | None
    noninteractive_surface: bool
    app_server_surface: bool
    existing_auth_reuse: str
    reason: str


class CodexCapabilityProbe:
    """Version/help-only probe. It never reads auth files or starts a task."""

    def probe(self) -> CodexCapability:
        executable = shutil.which("codex")
        if not executable:
            return CodexCapability("NOT_CONFIGURED", None, None, False, False, "NOT_CONFIRMED", "codex executable not found")
        try:
            version = subprocess.run([executable, "--version"], capture_output=True, text=True, shell=False,
                                     timeout=5, check=False)
            help_result = subprocess.run([executable, "--help"], capture_output=True, text=True, shell=False,
                                         timeout=5, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            return CodexCapability("PARTIAL", executable, None, False, False, "NOT_CONFIRMED", type(error).__name__)
        help_text = (help_result.stdout + "\n" + help_result.stderr).lower()
        app_server = "app-server" in help_text or "app server" in help_text
        noninteractive = any(marker in help_text for marker in ("exec", "json", "non-interactive", "app-server"))
        status = "AVAILABLE" if version.returncode == 0 and noninteractive else "PARTIAL"
        auth_reuse = "PARTIAL" if status == "AVAILABLE" else "NOT_CONFIRMED"
        return CodexCapability(
            status, executable, _bounded(version.stdout.strip() or version.stderr.strip(), 200),
            noninteractive, app_server, auth_reuse,
            "Version/help surface only; auth and task execution were not probed",
        )


class CodexTaskRunner(Protocol):
    def run_task(self, spec: CodexTaskSpec) -> StageResult: ...


class RealCodexRunner:
    """V0.1 adapter boundary; deliberately does not launch nested Codex tasks."""

    def __init__(self, capability: CodexCapability | None = None):
        self.capability = capability or CodexCapabilityProbe().probe()

    def run_task(self, spec: CodexTaskSpec) -> StageResult:
        spec.validate()
        return StageResult(
            StageResultStatus.BLOCKED,
            "Real Codex task execution is disabled pending independent review",
            error="REAL_CODEX_EXECUTION_REVIEW_PENDING",
            metrics={"capability": self.capability.status, "app_server": self.capability.app_server_surface},
        )


class WorkflowSupervisor:
    def __init__(self, repository: SupervisorRepository, runners: Mapping[WorkflowStage, StageRunner],
                 timeout_seconds: float = 120, retry_backoff_seconds: float = 0):
        self.repository = repository
        self.runners = dict(runners)
        self.timeout_seconds = timeout_seconds
        self.retry_backoff_seconds = max(0, retry_backoff_seconds)
        self.lock_ttl_seconds = max(LOCK_TTL_SECONDS, int(timeout_seconds) + 60)
        self.lock_token = str(uuid.uuid4())
        self.locked = False

    def acquire_singleton(self, pid: int | None = None) -> bool:
        self.locked = self.repository.acquire_lock(
            self.lock_token, pid or os.getpid(), ttl=self.lock_ttl_seconds,
        )
        return self.locked

    def release_singleton(self) -> None:
        if self.locked:
            self.repository.release_lock(self.lock_token)
            self.locked = False

    def heartbeat(self) -> bool:
        return self.locked and self.repository.heartbeat_lock(
            self.lock_token, ttl=self.lock_ttl_seconds,
        )

    def create_demo(self, owner_id: str, job_id: str | None = None) -> WorkflowJob:
        return self.repository.create_job(
            "SUPERVISOR_DEMO", owner_id, metadata={"supervisor_demo": True, "allow_git_mutation": False},
            max_review_rounds=2, max_attempts_per_stage=2, job_id=job_id,
        )

    def status(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        return (
            self.repository.get_job_for_owner(job_id, owner_id)
            if owner_id is not None else self.repository.get_job(job_id)
        )

    def list_jobs(self, owner_id: str | None = None, limit=50) -> list[WorkflowJob]:
        return self.repository.list_jobs(owner_id, limit)

    def pause(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        job = self.status(job_id, owner_id)
        if job.status in TERMINAL_JOB_STATUSES or job.resume_state == "PAUSED":
            return job
        state = "PAUSE_REQUESTED" if job.status is JobStatus.RUNNING else "PAUSED"
        status = JobStatus.RUNNING if job.status is JobStatus.RUNNING else JobStatus.WAITING
        job = self.repository.update_job(job_id, status=status, resume_state=state)
        self.repository.record_event(job_id, "JOB_PAUSED", job.current_stage, {"state": state}, f"pause:{job_id}:{state}")
        return job

    def resume(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        job = self.status(job_id, owner_id)
        if job.status is JobStatus.CANCELED or job.status is JobStatus.COMPLETED:
            return job
        if job.status is JobStatus.QUEUED and not job.resume_state:
            return job
        if job.resume_state not in {"PAUSED", "INTERRUPTED_SAFE_RETRY", "RETRY_SCHEDULED"}:
            return job
        job = self.repository.update_job(job_id, status=JobStatus.QUEUED, resume_state=None, next_retry_at=None)
        self.repository.record_event(job_id, "JOB_RESUMED", job.current_stage, {}, f"resume:{job_id}:{job.current_stage.value}:{job.attempt}")
        return job

    def cancel(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        job = self.status(job_id, owner_id)
        if job.status is JobStatus.CANCELED or job.status is JobStatus.COMPLETED:
            return job
        if job.status is JobStatus.RUNNING:
            job = self.repository.update_job(job_id, resume_state="CANCEL_REQUESTED")
        else:
            job = self.repository.update_job(job_id, status=JobStatus.CANCELED, resume_state=None)
        self.repository.record_event(job_id, "JOB_CANCELED", job.current_stage, {}, f"cancel:{job_id}")
        return job

    def retry(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        job = self.status(job_id, owner_id)
        if job.status not in {JobStatus.FAILED, JobStatus.BLOCKED}:
            return job
        if job.current_stage is WorkflowStage.GIT_GATE:
            return job
        if (job.resume_state == "BLOCKED_REQUIRES_RECONCILIATION"
                and job.current_stage in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}):
            return job
        if self.repository.stage_attempts(job_id, job.current_stage) >= job.max_attempts_per_stage:
            return job
        job = self.repository.update_job(job_id, status=JobStatus.QUEUED, resume_state=None,
                                         last_error=None, next_retry_at=None)
        self.repository.record_event(job_id, "RETRY_SCHEDULED", job.current_stage,
                                     {"manual": True}, f"retry:{job_id}:{job.current_stage.value}:{job.attempt}")
        return job

    def recover_interrupted(self) -> int:
        recovered = 0
        for job in self.repository.list_jobs(limit=200):
            if job.status is not JobStatus.RUNNING:
                continue
            with self.repository.db:
                self.repository.db.execute(
                    "UPDATE supervisor_stage_runs SET status='INTERRUPTED',completed_at=?,error='PROCESS_INTERRUPTED' WHERE job_id=? AND status='RUNNING'",
                    (utc_now(), job.job_id),
                )
            attempts = self.repository.stage_attempts(job.job_id, job.current_stage)
            if job.current_stage in SAFE_RECOVERY_STAGES and attempts < job.max_attempts_per_stage:
                self.repository.update_job(job.job_id, status=JobStatus.QUEUED,
                                           resume_state="INTERRUPTED_SAFE_RETRY", last_error="PROCESS_INTERRUPTED")
                self.repository.record_event(job.job_id, "RETRY_SCHEDULED", job.current_stage,
                                             {"reason": "PROCESS_INTERRUPTED"}, f"recover:{job.job_id}:{job.current_stage.value}:{attempts}")
            else:
                self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                           resume_state="BLOCKED_REQUIRES_RECONCILIATION", last_error="PROCESS_INTERRUPTED")
                self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                             {"reason": "RECONCILIATION_REQUIRED"}, f"blocked:{job.job_id}:{attempts}")
            recovered += 1
        return recovered

    def _schedule_failure(self, job: WorkflowJob, result: StageResult) -> WorkflowJob:
        attempts = self.repository.stage_attempts(job.job_id, job.current_stage)
        error = _safe_text(result.error or result.summary, 1000)
        if result.status is StageResultStatus.BLOCKED or job.current_stage in {WorkflowStage.SECURITY, WorkflowStage.GIT_GATE}:
            event = "SECURITY_FAILED" if job.current_stage is WorkflowStage.SECURITY else "GIT_GATE_BLOCKED"
            updated = self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                                 resume_state="BLOCKED_REQUIRES_RECONCILIATION", last_error=error)
            self.repository.record_event(job.job_id, event, job.current_stage, {"error": error})
            return updated
        if attempts < job.max_attempts_per_stage:
            delay = min(self.retry_backoff_seconds * (2 ** max(0, attempts - 1)), 60)
            updated = self.repository.update_job(job.job_id, status=JobStatus.WAITING,
                                                 resume_state="RETRY_SCHEDULED", last_error=error,
                                                 next_retry_at=time.time() + delay)
            self.repository.record_event(job.job_id, "RETRY_SCHEDULED", job.current_stage,
                                         {"attempt": attempts, "backoff_seconds": delay})
            return updated
        updated = self.repository.update_job(job.job_id, status=JobStatus.FAILED,
                                             resume_state=None, last_error=error)
        self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                     {"attempt": attempts, "error": error})
        return updated

    def run_once(self) -> WorkflowJob | None:
        if not self.locked:
            raise RuntimeError("single-instance lock required")
        self.heartbeat()
        job = self.repository.queued_job()
        if not job:
            return None
        if job.resume_state == "PAUSED":
            return job
        runner = self.runners.get(job.current_stage)
        if not runner:
            return self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                              resume_state="RUNNER_NOT_CONFIGURED", last_error="RUNNER_NOT_CONFIGURED")
        started = self.repository.begin_stage(job)
        if not started:
            return self.status(job.job_id)
        run_id, attempt, idempotency_key = started
        context = StageContext(self.status(job.job_id), job.current_stage, attempt,
                               idempotency_key, self.timeout_seconds, self.repository)
        try:
            result = runner.run(context)
        except Exception as error:
            result = StageResult.failed("Stage runner raised a bounded error", type(error).__name__)
        self.repository.finish_stage(run_id, job.job_id, job.current_stage, result)
        current = self.status(job.job_id)
        if current.resume_state == "CANCEL_REQUESTED":
            return self.repository.update_job(job.job_id, status=JobStatus.CANCELED, resume_state=None)

        if job.current_stage is WorkflowStage.REVIEW:
            self.repository.record_event(job.job_id, "REVIEW_FINDINGS_RECEIVED", job.current_stage,
                                         {"status": result.status.value, "findings_count": result.metrics.get("findings_count")})
            if result.status is StageResultStatus.FAIL:
                review_round = job.review_round + 1
                self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                             {"reason": "REVIEW_FAIL", "review_round": review_round})
                if review_round >= job.max_review_rounds:
                    updated = self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                                         review_round=review_round,
                                                         resume_state="MAX_REVIEW_ROUNDS", last_error="MAX_REVIEW_ROUNDS")
                    self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                                 {"reason": "MAX_REVIEW_ROUNDS", "review_round": review_round})
                    return updated
                updated = self.repository.update_job(job.job_id, status=JobStatus.QUEUED,
                                                     current_stage=WorkflowStage.REVISION,
                                                     review_round=review_round, attempt=0, resume_state=None)
                self.repository.record_event(job.job_id, "STAGE_COMPLETED", job.current_stage,
                                             {"result": "FAIL_TO_REVISION", "review_round": review_round})
                return updated

        if result.status is not StageResultStatus.PASS:
            return self._schedule_failure(job, result)

        next_stage = NEXT_STAGE[job.current_stage]
        if next_stage is WorkflowStage.DONE:
            updated = self.repository.update_job(job.job_id, status=JobStatus.COMPLETED,
                                                 current_stage=WorkflowStage.DONE, attempt=0,
                                                 resume_state=None, last_error=None)
            self.repository.record_event(job.job_id, "STAGE_COMPLETED", job.current_stage, {"result": "PASS"})
            self.repository.record_event(job.job_id, "JOB_COMPLETED", WorkflowStage.DONE, {})
            return updated
        pause_requested = current.resume_state == "PAUSE_REQUESTED"
        updated = self.repository.update_job(
            job.job_id, status=JobStatus.WAITING if pause_requested else JobStatus.QUEUED,
            current_stage=next_stage, attempt=0,
            resume_state="PAUSED" if pause_requested else None, last_error=None, next_retry_at=None,
        )
        self.repository.record_event(job.job_id, "STAGE_COMPLETED", job.current_stage, {"result": "PASS"})
        return updated

    def run_until_terminal(self, job_id: str, max_transitions=50) -> WorkflowJob:
        for _ in range(max_transitions):
            job = self.status(job_id)
            if job.status in TERMINAL_JOB_STATUSES or job.status is JobStatus.BLOCKED or job.resume_state == "PAUSED":
                return job
            if job.status is JobStatus.WAITING and job.resume_state == "RETRY_SCHEDULED" and (job.next_retry_at or 0) > time.time():
                return job
            self.run_once()
        job = self.repository.update_job(job_id, status=JobStatus.BLOCKED,
                                         resume_state="TRANSITION_LIMIT", last_error="TRANSITION_LIMIT")
        self.repository.record_event(job_id, "STAGE_FAILED", job.current_stage, {"reason": "TRANSITION_LIMIT"})
        return job


def default_demo_runners(real_validation=True) -> dict[WorkflowStage, StageRunner]:
    validation: StageRunner = LocalValidationRunner() if real_validation else StaticPassRunner("Mock local validation passed")
    return {
        WorkflowStage.INTAKE: StaticPassRunner("Intake schema validated"),
        WorkflowStage.PRODUCER: MockCodexRunner(),
        WorkflowStage.VALIDATION: validation,
        WorkflowStage.SELF_ACCEPTANCE: StaticPassRunner("Deterministic self acceptance passed"),
        WorkflowStage.REVIEW: MockReviewRunner(),
        WorkflowStage.REVISION: MockCodexRunner(),
        WorkflowStage.SECURITY: SecurityRunner(),
        WorkflowStage.GIT_GATE: GitGateRunner(),
    }
