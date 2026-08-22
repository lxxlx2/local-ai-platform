from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Mapping

from local_ai_control.services.security import SecretFirewall
from .supervisor_contracts import (
    AI_ROOT, SUPERVISOR_DB, LOCK_TTL_SECONDS, MAX_ACTIVE_JOBS, MAX_EVENTS_PER_JOB, MAX_TERMINAL_JOBS,
    MAX_MUTATING_JOBS_IN_SYSTEM,
    CandidateIdentityProvider, JobStatus, StageResult, StageResultStatus, WorkflowJob, WorkflowStage,
    _bounded, _json_exact, _safe_audit_value, _safe_json, _safe_metadata, _safe_text,
    ensure_private_directory, ensure_private_file, utc_now, OwnerPrivateContentStore,
)
from .supervisor_payloads import DurablePayloadMixin


class SupervisorRepository(DurablePayloadMixin):
    """Owner-private durable state and executable payloads for the workflow supervisor."""

    def __init__(self, path: Path = SUPERVISOR_DB, candidate_identity_provider=None):
        self.path = Path(path)
        self.candidate_identity_provider = candidate_identity_provider or CandidateIdentityProvider(AI_ROOT)
        ensure_private_directory(self.path.parent)
        self.db = sqlite3.connect(self.path, timeout=5)
        ensure_private_file(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.content_store = OwnerPrivateContentStore(self.path.parent / "content")

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
              metadata_json TEXT NOT NULL, next_retry_at REAL,
              baseline_commit_sha TEXT,
              mutation_capable INTEGER NOT NULL DEFAULT 1,
              baseline_candidate_state_sha256 TEXT,
              job_request_hash TEXT,
              cancel_requested_at TEXT, cancel_requested_by TEXT, target_execution_id TEXT
            );
            CREATE INDEX IF NOT EXISTS supervisor_jobs_queue_idx
              ON supervisor_jobs(status, next_retry_at, created_at);
            CREATE TABLE IF NOT EXISTS supervisor_stage_runs(
              run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
              attempt INTEGER NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
              completed_at TEXT, idempotency_key TEXT NOT NULL UNIQUE,
              summary TEXT, error TEXT, metrics_json TEXT NOT NULL DEFAULT '{}',
              review_round INTEGER NOT NULL DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS supervisor_work_units(
              work_unit_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner_id TEXT NOT NULL,
              stage TEXT NOT NULL, review_round INTEGER NOT NULL DEFAULT 0,
              repo_root TEXT NOT NULL, allowed_paths_json TEXT NOT NULL,
              risk_level TEXT NOT NULL, timeout_seconds REAL NOT NULL,
              model_role TEXT NOT NULL, expected_output_schema_json TEXT NOT NULL,
              prompt_content_ref TEXT NOT NULL, prompt_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL, status TEXT NOT NULL,
              safe_file_manifest_json TEXT,
              FOREIGN KEY(job_id) REFERENCES supervisor_jobs(job_id),
              UNIQUE(job_id, stage, review_round)
            );
            CREATE INDEX IF NOT EXISTS supervisor_work_units_job_idx
              ON supervisor_work_units(job_id, stage, review_round);
            CREATE TABLE IF NOT EXISTS supervisor_review_findings(
              finding_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner_id TEXT NOT NULL,
              review_round INTEGER NOT NULL, severity TEXT NOT NULL, file_path TEXT NOT NULL,
              evidence_summary TEXT NOT NULL, recommended_fix TEXT NOT NULL,
              created_at TEXT NOT NULL, integrity_hash TEXT NOT NULL,
              status TEXT NOT NULL, consumed_by_revision TEXT,
              finding_scope TEXT NOT NULL DEFAULT 'FILE',
              FOREIGN KEY(job_id) REFERENCES supervisor_jobs(job_id)
            );
            CREATE INDEX IF NOT EXISTS supervisor_review_findings_job_idx
              ON supervisor_review_findings(job_id, review_round, created_at);
            CREATE TABLE IF NOT EXISTS supervisor_executions(
              execution_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, work_unit_id TEXT NOT NULL,
              stage TEXT NOT NULL, run_id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
              started_at TEXT NOT NULL, completed_at TEXT, completion_status TEXT NOT NULL,
              result_hash TEXT, cancellation_status TEXT NOT NULL DEFAULT 'NOT_REQUESTED',
              cancel_requested_at TEXT, cancel_requested_by TEXT, target_execution_id TEXT,
              candidate_state_sha256 TEXT, candidate_tree_sha TEXT, candidate_diff_sha256 TEXT,
              FOREIGN KEY(job_id) REFERENCES supervisor_jobs(job_id),
              FOREIGN KEY(work_unit_id) REFERENCES supervisor_work_units(work_unit_id),
              FOREIGN KEY(run_id) REFERENCES supervisor_stage_runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS supervisor_executions_job_idx
              ON supervisor_executions(job_id,stage,started_at);
            CREATE TABLE IF NOT EXISTS supervisor_execution_fences(
              fence_name TEXT PRIMARY KEY, job_id TEXT NOT NULL, work_unit_id TEXT,
              execution_id TEXT, reason TEXT NOT NULL, created_at TEXT NOT NULL,
              status TEXT NOT NULL, requires_manual_reconciliation INTEGER NOT NULL,
              cleared_at TEXT, reconciliation_note_sha256 TEXT,
              FOREIGN KEY(job_id) REFERENCES supervisor_jobs(job_id)
            );
            CREATE INDEX IF NOT EXISTS supervisor_execution_fences_status_idx
              ON supervisor_execution_fences(status,created_at);
            """
        )
        migrations = (
            ("supervisor_jobs", "baseline_commit_sha", "TEXT"),
            ("supervisor_jobs", "mutation_capable", "INTEGER NOT NULL DEFAULT 1"),
            ("supervisor_jobs", "baseline_candidate_state_sha256", "TEXT"),
            ("supervisor_jobs", "job_request_hash", "TEXT"),
            ("supervisor_jobs", "cancel_requested_at", "TEXT"),
            ("supervisor_jobs", "cancel_requested_by", "TEXT"),
            ("supervisor_jobs", "target_execution_id", "TEXT"),
            ("supervisor_stage_runs", "review_round", "INTEGER NOT NULL DEFAULT 0"),
            ("supervisor_work_units", "safe_file_manifest_json", "TEXT"),
            ("supervisor_review_findings", "finding_scope", "TEXT NOT NULL DEFAULT 'FILE'"),
            ("supervisor_executions", "candidate_state_sha256", "TEXT"),
            ("supervisor_executions", "candidate_tree_sha", "TEXT"),
            ("supervisor_executions", "candidate_diff_sha256", "TEXT"),
            ("supervisor_executions", "cancel_requested_at", "TEXT"),
            ("supervisor_executions", "cancel_requested_by", "TEXT"),
            ("supervisor_executions", "target_execution_id", "TEXT"),
        )
        for table, column, definition in migrations:
            columns = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in columns:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        with self.db:
            for row in self.db.execute(
                "SELECT * FROM supervisor_jobs WHERE job_request_hash IS NULL OR job_request_hash=''"
            ).fetchall():
                digest = self._job_request_hash(
                    row["job_id"], row["owner_id"], row["title"], row["project_scope"],
                    row["risk_level"], row["created_by"], int(row["max_review_rounds"]),
                    int(row["max_attempts_per_stage"]), json.loads(row["metadata_json"]),
                    bool(row["mutation_capable"]),
                )
                self.db.execute("UPDATE supervisor_jobs SET job_request_hash=? WHERE job_id=?",
                                (digest, row["job_id"]))
        self.db.commit()
        for candidate in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if candidate.exists():
                ensure_private_file(candidate)

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
            baseline_commit_sha=row["baseline_commit_sha"],
            mutation_capable=bool(row["mutation_capable"]),
            baseline_candidate_state_sha256=row["baseline_candidate_state_sha256"],
        )

    @staticmethod
    def _job_request_hash(job_id: str, owner_id: str, title: str, project_scope: str,
                          risk_level: str, created_by: str, max_review_rounds: int,
                          max_attempts_per_stage: int, metadata: Mapping,
                          mutation_capable: bool) -> str:
        payload = {
            "job_id": job_id, "owner_id": str(owner_id), "title": title,
            "project_scope": str(Path(project_scope).resolve()), "risk_level": risk_level,
            "created_by": created_by, "max_review_rounds": int(max_review_rounds),
            "max_attempts_per_stage": int(max_attempts_per_stage),
            "metadata": dict(metadata), "mutation_capable": bool(mutation_capable),
        }
        return hashlib.sha256(_json_exact(payload, 64_000).encode()).hexdigest()

    def create_job(self, title: str, owner_id: str, project_scope: str = str(AI_ROOT),
                   risk_level: str = "LOW", created_by: str = "owner",
                   metadata: Mapping | None = None, max_review_rounds: int = 2,
                   max_attempts_per_stage: int = 2, job_id: str | None = None,
                   mutation_capable: bool = True) -> WorkflowJob:
        resolved = Path(project_scope).resolve()
        if resolved != AI_ROOT.resolve():
            raise PermissionError("project_scope must be /Users/jerson/AI in V0.1")
        if SecretFirewall().inspect(title).action == "BLOCK":
            raise ValueError("job title rejected by Secret Firewall")
        if not 1 <= max_review_rounds <= 5 or not 1 <= max_attempts_per_stage <= 5:
            raise ValueError("round/attempt limit outside safe range")
        if not mutation_capable:
            raise ValueError("READ_ONLY_PROBE_IS_NOT_A_WORKFLOW_JOB")
        supplied_metadata = dict(metadata or {})
        if {"baseline_commit_sha", "candidate_base_commit_sha"} & set(supplied_metadata):
            raise ValueError("trusted baseline cannot be supplied through metadata")
        now, identifier = utc_now(), job_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,46}", identifier):
            raise ValueError("job_id is not callback-safe")
        metadata_json = _safe_json(_safe_metadata(supplied_metadata), 16_000)
        request_hash = self._job_request_hash(
            identifier, owner_id, title, str(resolved), risk_level, created_by,
            max_review_rounds, max_attempts_per_stage, json.loads(metadata_json), True,
        )
        if job_id:
            existing = self.db.execute("SELECT * FROM supervisor_jobs WHERE job_id=?", (job_id,)).fetchone()
            if existing:
                if existing["job_request_hash"] != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self._job_from_row(existing)
        baseline = self.candidate_identity_provider.capture_baseline()
        baseline_identity = self.candidate_identity_provider.snapshot(baseline)
        if not bool(self.candidate_identity_provider.worktree_is_clean()):
            raise RuntimeError("WORKTREE_NOT_CLEAN")
        unowned = getattr(self.candidate_identity_provider, "unowned_write_root_paths", lambda: ())()
        if unowned:
            raise RuntimeError("WORKTREE_WRITE_ROOT_NOT_OWNABLE")
        baseline_state = hashlib.sha256(_json_exact(baseline_identity.stable_payload(), 1_000_000).encode()).hexdigest()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if mutation_capable:
                active = self.db.execute(
                    "SELECT COUNT(*) FROM supervisor_jobs WHERE mutation_capable=1 AND status IN (?,?,?,?)",
                    (JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.WAITING.value,
                     JobStatus.BLOCKED.value),
                ).fetchone()[0]
                if active >= MAX_MUTATING_JOBS_IN_SYSTEM:
                    self.db.rollback()
                    raise RuntimeError("MAX_MUTATING_JOBS_IN_SYSTEM=1")
            self.db.execute(
                "INSERT INTO supervisor_jobs "
                "(job_id,title,project_scope,created_at,updated_at,owner_id,risk_level,status,current_stage,attempt,"
                "review_round,max_review_rounds,max_attempts_per_stage,last_error,resume_state,created_by,"
                "metadata_json,next_retry_at,baseline_commit_sha,mutation_capable,baseline_candidate_state_sha256,"
                "job_request_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, _bounded(title, 200), str(resolved), now, now, str(owner_id), risk_level,
                 JobStatus.QUEUED.value, WorkflowStage.INTAKE.value, 0, 0, max_review_rounds,
                 max_attempts_per_stage, None, None, created_by,
                 metadata_json, None, baseline,
                 int(bool(mutation_capable)), baseline_state, request_hash),
            )
            self.record_event(identifier, "JOB_CREATED", WorkflowStage.INTAKE,
                              {"risk_level": risk_level}, commit=False)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
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
            sql += " WHERE owner_id=?"
            values.append(str(owner_id))
        sql += " ORDER BY created_at DESC LIMIT ?"
        values.append(min(max(limit, 1), 200))
        return [self._job_from_row(row) for row in self.db.execute(sql, values).fetchall()]

    def update_job(self, job_id: str, **changes) -> WorkflowJob:
        allowed = {"status", "current_stage", "attempt", "review_round", "last_error",
                   "resume_state", "next_retry_at"}
        if not changes or set(changes) - allowed:
            raise ValueError("invalid job update")
        values = {key: (value.value if isinstance(value, Enum) else value) for key, value in changes.items()}
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.db:
            self.db.execute(f"UPDATE supervisor_jobs SET {assignments} WHERE job_id=?", (*values.values(), job_id))
        return self.get_job(job_id)

    def update_job_metadata(self, job_id: str, metadata_patch: Mapping) -> WorkflowJob:
        if not isinstance(metadata_patch, Mapping):
            raise TypeError("metadata patch must be a Mapping")
        if {"baseline_commit_sha", "candidate_base_commit_sha"} & set(metadata_patch):
            raise ValueError("trusted baseline is immutable and cannot be patched")
        current = self.get_job(job_id)
        merged = dict(current.metadata)
        merged.update(dict(metadata_patch))
        try:
            json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("metadata patch must contain JSON-compatible values") from error
        encoded = json.dumps(_safe_metadata(merged), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > 16_000:
            raise ValueError("metadata patch exceeds safe persistence bound")
        with self.db:
            self.db.execute(
                "UPDATE supervisor_jobs SET metadata_json=?,updated_at=? WHERE job_id=?",
                (encoded, utc_now(), job_id),
            )
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
                    "DELETE FROM supervisor_events WHERE event_id IN ("
                    "SELECT event_id FROM supervisor_events WHERE job_id=? ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                    (job_id, MAX_EVENTS_PER_JOB),
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
        first_producer_identity = None
        mutating_stage = job.current_stage in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}
        if mutating_stage and not job.mutation_capable:
            raise PermissionError("read-only job cannot enter mutating stage")
        if mutating_stage:
            unowned = getattr(self.candidate_identity_provider, "unowned_write_root_paths", lambda: ())()
            if unowned:
                return None
        first_producer = (job.current_stage is WorkflowStage.PRODUCER
                          and self.stage_attempts(job.job_id, job.current_stage) == 0)
        if first_producer:
            if not job.baseline_commit_sha or not job.baseline_candidate_state_sha256:
                return None
            first_producer_identity = self.candidate_identity_provider.snapshot(job.baseline_commit_sha)
            if not bool(self.candidate_identity_provider.worktree_is_clean()):
                return None
            current_hash = hashlib.sha256(
                _json_exact(first_producer_identity.stable_payload(), 1_000_000).encode()
            ).hexdigest()
            if current_hash != job.baseline_candidate_state_sha256:
                return None
        try:
            self.db.execute("BEGIN IMMEDIATE")
            durable = self.db.execute("SELECT * FROM supervisor_jobs WHERE job_id=?", (job.job_id,)).fetchone()
            if (not durable or durable["current_stage"] != job.current_stage.value
                    or (job.current_stage in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}
                        and not bool(durable["mutation_capable"]))):
                self.db.rollback()
                return None
            if (job.current_stage in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}
                    and self.has_mutation_guard()):
                self.db.rollback()
                return None
            active = self.db.execute(
                "SELECT COUNT(*) FROM supervisor_jobs WHERE status=? AND job_id<>?",
                (JobStatus.RUNNING.value, job.job_id),
            ).fetchone()[0]
            if active >= MAX_ACTIVE_JOBS:
                self.db.rollback()
                return None
            attempt = self.stage_attempts(job.job_id, job.current_stage) + 1
            key = f"{job.job_id}:{job.current_stage.value}:{attempt}"
            run_id = str(uuid.uuid4())
            effective_review_round = job.review_round + 1 if job.current_stage is WorkflowStage.REVIEW else job.review_round
            self.db.execute(
                "INSERT INTO supervisor_stage_runs"
                "(run_id,job_id,stage,attempt,status,started_at,idempotency_key,review_round) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (run_id, job.job_id, job.current_stage.value, attempt, "RUNNING", utc_now(), key,
                 effective_review_round),
            )
            self.db.execute(
                "UPDATE supervisor_jobs SET status=?,attempt=?,updated_at=?,last_error=NULL WHERE job_id=?",
                (JobStatus.RUNNING.value, attempt, utc_now(), job.job_id),
            )
            self.record_event(job.job_id, "STAGE_STARTED", job.current_stage,
                              {"attempt": attempt}, f"started:{key}", commit=False)
            self.db.commit()
            return run_id, attempt, key
        except sqlite3.IntegrityError:
            self.db.rollback()
            return None
        except sqlite3.Error:
            self.db.rollback()
            raise
        except Exception:
            if self.db.in_transaction:
                self.db.rollback()
            raise

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

    def latest_stage_runs(self, job_id: str) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM supervisor_stage_runs WHERE job_id=? ORDER BY started_at", (job_id,)
        ).fetchall()]

    @staticmethod
    def _validate_execution_id(execution_id: str) -> str:
        try:
            return str(uuid.UUID(str(execution_id)))
        except (ValueError, AttributeError) as error:
            raise ValueError("execution_id must be a canonical UUID") from error

    def start_execution(self, execution_id: str, job_id: str, work_unit_id: str,
                        stage: WorkflowStage, idempotency_key: str, provider: str) -> dict:
        identifier = self._validate_execution_id(execution_id)
        if stage not in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}:
            raise ValueError("execution records are restricted to mutating stages")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", str(provider)):
            raise ValueError("unsafe execution provider identifier")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if self.has_mutation_guard(exclude_execution_id=identifier):
                self.db.rollback()
                raise ValueError("active mutation fence denies execution start")
            run = self.db.execute(
                "SELECT * FROM supervisor_stage_runs WHERE idempotency_key=?", (idempotency_key,),
            ).fetchone()
            unit = self.db.execute(
                "SELECT * FROM supervisor_work_units WHERE work_unit_id=?", (work_unit_id,),
            ).fetchone()
            durable_job = self.db.execute(
                "SELECT mutation_capable,resume_state FROM supervisor_jobs WHERE job_id=?", (job_id,),
            ).fetchone()
            if (not run or run["job_id"] != job_id or run["stage"] != stage.value or run["status"] != "RUNNING"
                    or not unit or unit["job_id"] != job_id or unit["stage"] != stage.value
                    or int(unit["review_round"]) != int(run["review_round"])
                    or not durable_job or not bool(durable_job["mutation_capable"])
                    or durable_job["resume_state"] == "CANCEL_REQUESTED"):
                self.db.rollback()
                raise ValueError("execution binding does not match durable run/work unit")
            existing = self.db.execute(
                "SELECT * FROM supervisor_executions WHERE execution_id=?", (identifier,),
            ).fetchone()
            if existing:
                expected = (job_id, work_unit_id, stage.value, run["run_id"], str(provider))
                actual = tuple(existing[key] for key in ("job_id", "work_unit_id", "stage", "run_id", "provider"))
                if actual != expected:
                    self.db.rollback()
                    raise ValueError("execution_id conflicts with durable binding")
                self.db.commit()
                return dict(existing)
            self.db.execute(
                "INSERT INTO supervisor_executions"
                "(execution_id,job_id,work_unit_id,stage,run_id,provider,started_at,completion_status) "
                "VALUES(?,?,?,?,?,?,?,'STARTED')",
                (identifier, job_id, work_unit_id, stage.value, run["run_id"], str(provider), utc_now()),
            )
            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()
            raise
        return dict(self.db.execute(
            "SELECT * FROM supervisor_executions WHERE execution_id=?", (identifier,),
        ).fetchone())

    def complete_execution(self, execution_id: str, result: StageResult) -> dict:
        identifier = self._validate_execution_id(execution_id)
        digest = hashlib.sha256(_json_exact({
            "status": result.status.value,
            "summary_sha256": hashlib.sha256(str(result.summary).encode()).hexdigest(),
            "error_sha256": hashlib.sha256(str(result.error or "").encode()).hexdigest(),
        }, 2_000).encode()).hexdigest()
        initial = self.db.execute(
            "SELECT e.*,j.baseline_commit_sha FROM supervisor_executions e JOIN supervisor_jobs j "
            "ON j.job_id=e.job_id WHERE e.execution_id=?", (identifier,),
        ).fetchone()
        if not initial or initial["completion_status"] not in {"STARTED", "CANCELLATION_PENDING"}:
            raise ValueError("execution is not in a completable state")
        identity = None
        snapshot_failed = False
        unaccounted_ignored_mutation = False
        if result.status is StageResultStatus.PASS and initial["cancellation_status"] == "NOT_REQUESTED":
            try:
                if self.candidate_identity_provider.unowned_write_root_paths():
                    unaccounted_ignored_mutation = True
                    raise PermissionError("UNTRACKED_IGNORED_MUTATION_UNACCOUNTED")
                identity = self.candidate_identity_provider.snapshot(initial["baseline_commit_sha"])
            except Exception:
                snapshot_failed = True
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT * FROM supervisor_executions WHERE execution_id=?", (identifier,),
            ).fetchone()
            if not row or row["completion_status"] not in {"STARTED", "CANCELLATION_PENDING"}:
                self.db.rollback()
                raise ValueError("execution is not in a completable state")
            confirm = (result.status is StageResultStatus.PASS and not snapshot_failed and identity is not None
                       and row["completion_status"] == "STARTED"
                       and row["cancellation_status"] == "NOT_REQUESTED"
                       and not self.has_mutation_guard(exclude_execution_id=identifier))
            status = "COMPLETED_CONFIRMED" if confirm else ("UNKNOWN" if snapshot_failed else "COMPLETED_NONPASS")
            state_hash = (hashlib.sha256(_json_exact(identity.stable_payload(), 1_000_000).encode()).hexdigest()
                          if identity else None)
            self.db.execute(
                "UPDATE supervisor_executions SET completed_at=?,completion_status=?,result_hash=?,"
                "candidate_state_sha256=?,candidate_tree_sha=?,candidate_diff_sha256=? "
                "WHERE execution_id=? AND completion_status IN ('STARTED','CANCELLATION_PENDING')",
                (utc_now(), status, digest, state_hash, identity.candidate_tree_sha if identity else None,
                 identity.candidate_diff_sha256 if identity else None, identifier),
            )
            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()
            raise
        completed = dict(self.db.execute(
            "SELECT * FROM supervisor_executions WHERE execution_id=?", (identifier,),
        ).fetchone())
        if unaccounted_ignored_mutation:
            self.persist_mutation_fence(
                initial["job_id"], "UNTRACKED_IGNORED_MUTATION_UNACCOUNTED",
                initial["work_unit_id"], identifier,
            )
        return completed

    def record_execution_cancellation(self, execution_id: str, cancellation_status: str) -> None:
        identifier = self._validate_execution_id(execution_id)
        if cancellation_status not in {"CANCELED", "FAILED", "UNSUPPORTED"}:
            raise ValueError("invalid execution cancellation status")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM supervisor_executions WHERE execution_id=?", (identifier,)).fetchone()
            if not row:
                self.db.rollback(); raise KeyError("execution record not found")
            if row["completion_status"] == "COMPLETED_CONFIRMED":
                self.db.rollback(); raise ValueError("confirmed execution cannot be canceled")
            self.db.execute(
                "UPDATE supervisor_executions SET cancellation_status=?,completion_status="
                "CASE WHEN completion_status='STARTED' THEN 'CANCELLATION_PENDING' ELSE completion_status END "
                "WHERE execution_id=?", (cancellation_status, identifier),
            )
            self.db.commit()
        except sqlite3.Error:
            self.db.rollback(); raise

    @staticmethod
    def record_execution_cancellation_external(path: Path, execution_id: str,
                                               cancellation_status: str) -> bool:
        try:
            identifier = str(uuid.UUID(str(execution_id)))
        except (ValueError, AttributeError):
            return False
        if cancellation_status not in {"CANCELED", "FAILED", "UNSUPPORTED"}:
            return False
        try:
            db = sqlite3.connect(path, timeout=2)
            with db:
                cursor = db.execute(
                    "UPDATE supervisor_executions SET cancellation_status=?,completion_status="
                    "CASE WHEN completion_status='STARTED' THEN 'CANCELLATION_PENDING' ELSE completion_status END "
                    "WHERE execution_id=? AND completion_status!='COMPLETED_CONFIRMED'",
                    (cancellation_status, identifier),
                )
            db.close()
            return cursor.rowcount == 1
        except sqlite3.Error:
            return False

    def confirmed_execution_for_run(self, run_id: str, job_id: str, stage: WorkflowStage) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM supervisor_executions WHERE run_id=? AND job_id=? AND stage=? "
            "AND completion_status='COMPLETED_CONFIRMED' AND completed_at IS NOT NULL "
            "AND cancellation_status='NOT_REQUESTED'",
            (run_id, job_id, stage.value),
        ).fetchone()
        if not row or self.has_mutation_guard(exclude_execution_id=row["execution_id"]):
            return None
        job = self.get_job(job_id)
        try:
            current = self.candidate_identity_provider.snapshot(job.baseline_commit_sha)
        except Exception:
            return None
        state_hash = hashlib.sha256(_json_exact(current.stable_payload(), 1_000_000).encode()).hexdigest()
        if (state_hash != row["candidate_state_sha256"] or current.candidate_tree_sha != row["candidate_tree_sha"]
                or current.candidate_diff_sha256 != row["candidate_diff_sha256"]):
            return None
        return dict(row)

    def active_execution_for_job(self, job_id: str, stage: WorkflowStage) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM supervisor_executions WHERE job_id=? AND stage=? "
            "AND completion_status IN ('STARTED','CANCELLATION_PENDING') ORDER BY started_at DESC LIMIT 1",
            (job_id, stage.value),
        ).fetchone()
        return dict(row) if row else None

    def request_execution_cancellation(self, job_id: str, owner_id: str,
                                       stage: WorkflowStage) -> dict | None:
        """Persist an Owner request; the daemon execution owner performs provider cancellation."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            job = self.db.execute("SELECT * FROM supervisor_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not job:
                self.db.rollback(); raise KeyError("job not found")
            if job["owner_id"] != str(owner_id):
                self.db.rollback(); raise PermissionError("supervisor job owner mismatch")
            if job["current_stage"] != stage.value or job["status"] != JobStatus.RUNNING.value:
                self.db.rollback(); raise ValueError("cancel request requires matching running stage")
            execution = self.db.execute(
                "SELECT * FROM supervisor_executions WHERE job_id=? AND stage=? "
                "AND completion_status IN ('STARTED','CANCELLATION_PENDING') ORDER BY started_at DESC LIMIT 1",
                (job_id, stage.value),
            ).fetchone()
            target = execution["execution_id"] if execution else None
            existing_target = job["target_execution_id"]
            if job["resume_state"] == "CANCEL_REQUESTED" and existing_target not in {None, target}:
                self.db.rollback(); raise ValueError("cancel request execution binding mismatch")
            requested_at = job["cancel_requested_at"] or utc_now()
            self.db.execute(
                "UPDATE supervisor_jobs SET resume_state='CANCEL_REQUESTED',cancel_requested_at=?,"
                "cancel_requested_by=?,target_execution_id=?,updated_at=? WHERE job_id=?",
                (requested_at, str(owner_id), target, utc_now(), job_id),
            )
            if execution:
                self.db.execute(
                    "UPDATE supervisor_executions SET cancellation_status='REQUESTED',"
                    "completion_status=CASE WHEN completion_status='STARTED' THEN 'CANCELLATION_PENDING' "
                    "ELSE completion_status END,cancel_requested_at=?,cancel_requested_by=?,target_execution_id=? "
                    "WHERE execution_id=? AND job_id=? "
                    "AND cancellation_status IN ('NOT_REQUESTED','REQUESTED')",
                    (requested_at, str(owner_id), target, target, job_id),
                )
            self.db.commit()
            return dict(execution) if execution else None
        except sqlite3.Error:
            self.db.rollback(); raise

    @staticmethod
    def pending_cancel_request_external(path: Path, job_id: str,
                                        execution_id: str) -> bool:
        try:
            identifier = str(uuid.UUID(str(execution_id)))
            db = sqlite3.connect(path, timeout=2)
            row = db.execute(
                "SELECT 1 FROM supervisor_jobs j JOIN supervisor_executions e ON e.job_id=j.job_id "
                "WHERE j.job_id=? AND j.resume_state='CANCEL_REQUESTED' "
                "AND j.target_execution_id=? AND e.execution_id=? AND e.target_execution_id=? "
                "AND e.cancellation_status='REQUESTED'",
                (job_id, identifier, identifier, identifier),
            ).fetchone()
            db.close()
            return bool(row)
        except (sqlite3.Error, ValueError, AttributeError):
            return False

    @staticmethod
    def mark_cancel_reconciliation_external(path: Path, job_id: str,
                                            execution_id: str) -> bool:
        try:
            identifier = str(uuid.UUID(str(execution_id)))
            db = sqlite3.connect(path, timeout=2)
            now = utc_now()
            with db:
                cursor = db.execute(
                    "UPDATE supervisor_jobs SET status=?,resume_state=?,last_error=?,updated_at=? "
                    "WHERE job_id=? AND target_execution_id=? AND resume_state='CANCEL_REQUESTED'",
                    (JobStatus.BLOCKED.value, "BLOCKED_REQUIRES_RECONCILIATION",
                     "OWNER_CANCEL_UNCONFIRMED", now, job_id, identifier),
                )
                db.execute(
                    "INSERT OR IGNORE INTO supervisor_events VALUES(?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), job_id, "CANCEL_REQUIRES_RECONCILIATION", None,
                     now, "{}", f"cancel-reconciliation:{job_id}:{identifier}"),
                )
            db.close()
            return cursor.rowcount == 1
        except (sqlite3.Error, ValueError, AttributeError):
            return False

    def _active_mutation_fence_row(self):
        return self.db.execute(
            "SELECT * FROM supervisor_execution_fences WHERE status='ACTIVE' "
            "AND requires_manual_reconciliation=1 ORDER BY created_at LIMIT 1"
        ).fetchone()

    def active_mutation_fence(self) -> dict | None:
        row = self._active_mutation_fence_row()
        return dict(row) if row else None

    def has_active_mutation_fence(self) -> bool:
        return self._active_mutation_fence_row() is not None

    def _unresolved_execution_row(self, exclude_execution_id: str | None = None):
        sql = ("SELECT * FROM supervisor_executions WHERE completion_status IN "
               "('STARTED','CANCELLATION_PENDING','UNKNOWN')")
        values: tuple = ()
        if exclude_execution_id:
            sql += " AND execution_id<>?"
            values = (exclude_execution_id,)
        return self.db.execute(sql + " ORDER BY started_at LIMIT 1", values).fetchone()

    def has_mutation_guard(self, exclude_execution_id: str | None = None) -> bool:
        return self._active_mutation_fence_row() is not None or self._unresolved_execution_row(exclude_execution_id) is not None

    def ensure_unresolved_execution_fences(self) -> int:
        rows = self.db.execute(
            "SELECT * FROM supervisor_executions WHERE completion_status IN "
            "('STARTED','CANCELLATION_PENDING','UNKNOWN') ORDER BY started_at"
        ).fetchall()
        created = 0
        for row in rows:
            before = self.db.execute(
                "SELECT 1 FROM supervisor_execution_fences WHERE fence_name=?", (f"mutation:{row['execution_id']}",),
            ).fetchone()
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO supervisor_execution_fences"
                    "(fence_name,job_id,work_unit_id,execution_id,reason,created_at,status,"
                    "requires_manual_reconciliation) VALUES(?,?,?,?,?,?,'ACTIVE',1)",
                    (f"mutation:{row['execution_id']}", row["job_id"], row["work_unit_id"],
                     row["execution_id"], "EXTERNAL_EXECUTION_UNCERTAIN", utc_now()),
                )
            created += int(before is None)
        return created

    def persist_mutation_fence(self, job_id: str, reason: str,
                               work_unit_id: str | None = None,
                               execution_id: str | None = None) -> dict:
        job = self.get_job(job_id)
        if job.current_stage not in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}:
            raise ValueError("mutation fence requires a mutating job stage")
        identifier = self._validate_execution_id(execution_id) if execution_id else None
        if identifier:
            execution = self.db.execute(
                "SELECT * FROM supervisor_executions WHERE execution_id=? AND job_id=?",
                (identifier, job_id),
            ).fetchone()
            if not execution:
                raise ValueError("mutation fence execution binding mismatch")
            work_unit_id = execution["work_unit_id"]
            basis = identifier
        else:
            run = self.db.execute(
                "SELECT run_id FROM supervisor_stage_runs WHERE job_id=? AND stage=? AND status='RUNNING' "
                "ORDER BY started_at DESC LIMIT 1", (job_id, job.current_stage.value),
            ).fetchone()
            basis = run["run_id"] if run else job_id
        fence_name = f"mutation:{basis}"
        safe_reason = _safe_text(reason, 200) or "EXTERNAL_EXECUTION_UNCERTAIN"
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO supervisor_execution_fences"
                "(fence_name,job_id,work_unit_id,execution_id,reason,created_at,status,requires_manual_reconciliation) "
                "VALUES(?,?,?,?,?,?,'ACTIVE',1)",
                (fence_name, job_id, work_unit_id, identifier, safe_reason, utc_now()),
            )
        return dict(self.db.execute(
            "SELECT * FROM supervisor_execution_fences WHERE fence_name=?", (fence_name,),
        ).fetchone())

    @staticmethod
    def persist_mutation_fence_external(path: Path, job_id: str, reason: str,
                                        work_unit_id: str | None = None,
                                        execution_id: str | None = None) -> bool:
        try:
            identifier = str(uuid.UUID(str(execution_id))) if execution_id else None
        except (ValueError, AttributeError):
            return False
        try:
            db = sqlite3.connect(path, timeout=2)
            db.row_factory = sqlite3.Row
            job = db.execute("SELECT * FROM supervisor_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not job or job["current_stage"] not in {WorkflowStage.PRODUCER.value, WorkflowStage.REVISION.value}:
                db.close()
                return False
            if identifier:
                execution = db.execute(
                    "SELECT * FROM supervisor_executions WHERE execution_id=? AND job_id=?",
                    (identifier, job_id),
                ).fetchone()
                if not execution:
                    db.close()
                    return False
                work_unit_id = execution["work_unit_id"]
                basis = identifier
            else:
                if work_unit_id:
                    unit = db.execute(
                        "SELECT 1 FROM supervisor_work_units WHERE work_unit_id=? AND job_id=?",
                        (work_unit_id, job_id),
                    ).fetchone()
                    if not unit:
                        work_unit_id = None
                run = db.execute(
                    "SELECT run_id FROM supervisor_stage_runs WHERE job_id=? AND stage=? AND status='RUNNING' "
                    "ORDER BY started_at DESC LIMIT 1", (job_id, job["current_stage"]),
                ).fetchone()
                basis = run["run_id"] if run else job_id
            fence_name = f"mutation:{basis}"
            safe_reason = reason if reason in {
                "EXTERNAL_EXECUTION_UNCERTAIN", "LEASE_LOST_CANCELLATION_FAILED",
                "LEASE_LOST_CANCELLATION_UNSUPPORTED",
            } else "EXTERNAL_EXECUTION_UNCERTAIN"
            with db:
                db.execute(
                    "INSERT OR IGNORE INTO supervisor_execution_fences"
                    "(fence_name,job_id,work_unit_id,execution_id,reason,created_at,status,"
                    "requires_manual_reconciliation) VALUES(?,?,?,?,?,?,'ACTIVE',1)",
                    (fence_name, job_id, work_unit_id, identifier, safe_reason, utc_now()),
                )
            row = db.execute(
                "SELECT status FROM supervisor_execution_fences WHERE fence_name=?", (fence_name,),
            ).fetchone()
            db.close()
            return bool(row and row["status"] == "ACTIVE")
        except sqlite3.Error:
            return False

    def _reconcile_mutation_fence_atomic(self, fence_name: str, reconciliation_note: str,
                                         owner_id: str | None = None,
                                         expected_job_id: str | None = None) -> dict:
        if not reconciliation_note or SecretFirewall().inspect(reconciliation_note).action == "BLOCK":
            raise ValueError("manual reconciliation note is required and must be secret-free")
        digest = hashlib.sha256(reconciliation_note.encode()).hexdigest()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            fence = self.db.execute(
                "SELECT * FROM supervisor_execution_fences WHERE fence_name=? AND status='ACTIVE'", (fence_name,),
            ).fetchone()
            if not fence:
                self.db.rollback()
                raise ValueError("active mutation fence not found")
            job = self.db.execute(
                "SELECT * FROM supervisor_jobs WHERE job_id=?", (fence["job_id"],),
            ).fetchone()
            if (not job or (expected_job_id is not None and job["job_id"] != expected_job_id)
                    or (owner_id is not None and job["owner_id"] != str(owner_id))):
                self.db.rollback()
                raise PermissionError("active manual-reconciliation fence binding not found")
            if (job["status"] != JobStatus.BLOCKED.value
                    or job["resume_state"] != "BLOCKED_REQUIRES_RECONCILIATION"):
                self.db.rollback()
                raise ValueError("job is not in the manual reconciliation state")
            lock = self.db.execute("SELECT * FROM supervisor_locks WHERE lock_name='consumer'").fetchone()
            if lock and float(lock["expires_at"]) > time.time():
                self.db.rollback()
                raise RuntimeError("SUPERVISOR_CONSUMER_STILL_ACTIVE")
            if fence["execution_id"]:
                self.db.execute(
                    "UPDATE supervisor_executions SET completion_status='MANUALLY_RECONCILED',completed_at=COALESCE(completed_at,?) "
                    "WHERE execution_id=? AND completion_status IN ('STARTED','CANCELLATION_PENDING','UNKNOWN')",
                    (utc_now(), fence["execution_id"]),
                )
            cursor = self.db.execute(
                "UPDATE supervisor_execution_fences SET status='CLEARED',requires_manual_reconciliation=0,"
                "cleared_at=?,reconciliation_note_sha256=? WHERE fence_name=? AND status='ACTIVE' "
                "AND requires_manual_reconciliation=1",
                (utc_now(), digest, fence_name),
            )
            if cursor.rowcount != 1:
                self.db.rollback()
                raise ValueError("active mutation fence not found")
            self.db.execute(
                "UPDATE supervisor_jobs SET status=?,resume_state=NULL,last_error=?,updated_at=? "
                "WHERE job_id=? AND status=? AND resume_state='BLOCKED_REQUIRES_RECONCILIATION'",
                (JobStatus.FAILED.value, "MANUAL_RECONCILIATION_COMPLETED", utc_now(),
                 job["job_id"], JobStatus.BLOCKED.value),
            )
            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()
            raise
        return dict(self.db.execute(
            "SELECT * FROM supervisor_execution_fences WHERE fence_name=?", (fence_name,),
        ).fetchone())

    def reconcile_mutation_fence(self, fence_name: str, reconciliation_note: str) -> dict:
        return self._reconcile_mutation_fence_atomic(fence_name, reconciliation_note)

    def reconcile_mutation_fence_for_owner(self, job_id: str, owner_id: str,
                                           fence_name: str, reconciliation_note: str) -> dict:
        return self._reconcile_mutation_fence_atomic(
            fence_name, reconciliation_note, str(owner_id), job_id,
        )

    def prune_terminal_jobs(self, keep: int = MAX_TERMINAL_JOBS) -> int:
        keep = min(max(int(keep), 1), MAX_TERMINAL_JOBS)
        rows = self.db.execute(
            "SELECT job_id FROM supervisor_jobs WHERE status IN (?,?,?,?) "
            "AND NOT EXISTS (SELECT 1 FROM supervisor_execution_fences f WHERE f.job_id=supervisor_jobs.job_id "
            "AND f.status='ACTIVE' AND f.requires_manual_reconciliation=1) "
            "ORDER BY updated_at DESC LIMIT -1 OFFSET ?",
            (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELED.value,
             JobStatus.BLOCKED.value, keep),
        ).fetchall()
        identifiers = [row["job_id"] for row in rows]
        with self.db:
            for job_id in identifiers:
                units = self.db.execute(
                    "SELECT prompt_content_ref FROM supervisor_work_units WHERE job_id=?", (job_id,)
                ).fetchall()
                for unit in units:
                    self.content_store.delete(unit["prompt_content_ref"])
                self.db.execute("DELETE FROM supervisor_execution_fences WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_executions WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_review_findings WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_work_units WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_artifacts WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_stage_runs WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_events WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_jobs WHERE job_id=?", (job_id,))
        return len(identifiers)

    def acquire_lock(self, owner_token: str, pid: int, ttl=LOCK_TTL_SECONDS) -> bool:
        now = time.time()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM supervisor_locks WHERE lock_name='consumer'").fetchone()
            if row and row["owner_token"] != owner_token and row["expires_at"] > now:
                self.db.rollback()
                return False
            self.db.execute(
                "INSERT OR REPLACE INTO supervisor_locks VALUES('consumer',?,?,?,?,?)",
                (owner_token, pid, now, now, now + ttl),
            )
            self.db.commit()
            return True
        except sqlite3.Error:
            self.db.rollback()
            return False

    def heartbeat_lock(self, owner_token: str, ttl=LOCK_TTL_SECONDS) -> bool:
        now = time.time()
        try:
            with self.db:
                cursor = self.db.execute(
                    "UPDATE supervisor_locks SET heartbeat_at=?,expires_at=? WHERE lock_name='consumer' AND owner_token=?",
                    (now, now + ttl, owner_token),
                )
            return cursor.rowcount == 1
        except sqlite3.Error:
            return False

    def release_lock(self, owner_token: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM supervisor_locks WHERE lock_name='consumer' AND owner_token=?", (owner_token,))

    def lock_snapshot(self) -> dict | None:
        row = self.db.execute("SELECT * FROM supervisor_locks WHERE lock_name='consumer'").fetchone()
        return dict(row) if row else None

    def queued_job(self, job_id: str | None = None) -> WorkflowJob | None:
        now = time.time()
        values: list[object] = [JobStatus.QUEUED.value, JobStatus.WAITING.value, now]
        sql = (
            "SELECT * FROM supervisor_jobs WHERE "
            "(status=? OR (status=? AND resume_state='RETRY_SCHEDULED' AND COALESCE(next_retry_at,0)<=?))"
        )
        if job_id is not None:
            sql += " AND job_id=?"
            values.append(job_id)
        sql += " ORDER BY created_at LIMIT 1"
        row = self.db.execute(sql, values).fetchone()
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
        fence = self._active_mutation_fence_row()
        unresolved_rows = self.db.execute(
            "SELECT e.completion_status,e.stage,j.status AS job_status,j.current_stage "
            "FROM supervisor_executions e JOIN supervisor_jobs j ON j.job_id=e.job_id "
            "WHERE e.completion_status IN ('STARTED','CANCELLATION_PENDING','UNKNOWN')",
        ).fetchall()
        lock_live = bool(lock and float(lock["expires_at"]) > time.time())
        orphaned = sum(
            1 for row in unresolved_rows
            if not (lock_live and row["completion_status"] in {"STARTED", "CANCELLATION_PENDING"}
                    and row["job_status"] == JobStatus.RUNNING.value
                    and row["stage"] == row["current_stage"])
        )
        reconciliation = bool(fence and fence["requires_manual_reconciliation"])
        recovery_required = bool(orphaned and not reconciliation)
        return {
            "status": ("BLOCKED_RECONCILIATION_REQUIRED" if reconciliation else
                       ("RECOVERY_REQUIRED" if recovery_required else "HEALTHY")),
            "db_reachable": True,
            "single_instance_lock": lock_live,
            "consumer_lock_live": lock_live,
            "active_jobs": counts["active"], "queue_depth": counts["queued"],
            "last_completed_job": dict(last) if last else None,
            "last_error": error["last_error"] if error else None,
            "requires_manual_reconciliation": reconciliation,
            "active_fence_name": fence["fence_name"] if fence else None,
            "blocked_job_id": fence["job_id"] if fence else None,
            "unresolved_execution_count": len(unresolved_rows),
            "orphan_unresolved_execution_count": orphaned,
            "recovery_required": recovery_required,
        }
