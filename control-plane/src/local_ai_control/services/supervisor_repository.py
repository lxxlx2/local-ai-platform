from __future__ import annotations

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
    JobStatus, StageResult, WorkflowJob, WorkflowStage, _bounded, _safe_audit_value, _safe_json,
    _safe_metadata, _safe_text, ensure_private_directory, ensure_private_file, utc_now, OwnerPrivateContentStore,
)
from .supervisor_payloads import DurablePayloadMixin


class SupervisorRepository(DurablePayloadMixin):
    """Owner-private durable state and executable payloads for the workflow supervisor."""

    def __init__(self, path: Path = SUPERVISOR_DB):
        self.path = Path(path)
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
            CREATE TABLE IF NOT EXISTS supervisor_work_units(
              work_unit_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner_id TEXT NOT NULL,
              stage TEXT NOT NULL, review_round INTEGER NOT NULL DEFAULT 0,
              repo_root TEXT NOT NULL, allowed_paths_json TEXT NOT NULL,
              risk_level TEXT NOT NULL, timeout_seconds REAL NOT NULL,
              model_role TEXT NOT NULL, expected_output_schema_json TEXT NOT NULL,
              prompt_content_ref TEXT NOT NULL, prompt_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL, status TEXT NOT NULL,
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
              FOREIGN KEY(job_id) REFERENCES supervisor_jobs(job_id)
            );
            CREATE INDEX IF NOT EXISTS supervisor_review_findings_job_idx
              ON supervisor_review_findings(job_id, review_round, created_at);
            """
        )
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
        )

    def create_job(self, title: str, owner_id: str, project_scope: str = str(AI_ROOT),
                   risk_level: str = "LOW", created_by: str = "owner",
                   metadata: Mapping | None = None, max_review_rounds: int = 2,
                   max_attempts_per_stage: int = 2, job_id: str | None = None) -> WorkflowJob:
        resolved = Path(project_scope).resolve()
        if resolved != AI_ROOT.resolve():
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
                "INSERT INTO supervisor_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, _bounded(title, 200), str(resolved), now, now, str(owner_id), risk_level,
                 JobStatus.QUEUED.value, WorkflowStage.INTAKE.value, 0, 0, max_review_rounds,
                 max_attempts_per_stage, None, None, created_by,
                 _safe_json(_safe_metadata(metadata), 16_000), None),
            )
            self.record_event(identifier, "JOB_CREATED", WorkflowStage.INTAKE,
                              {"risk_level": risk_level}, commit=False)
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
        try:
            self.db.execute("BEGIN IMMEDIATE")
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
            self.db.execute(
                "INSERT INTO supervisor_stage_runs(run_id,job_id,stage,attempt,status,started_at,idempotency_key) "
                "VALUES(?,?,?,?,?,?,?)",
                (run_id, job.job_id, job.current_stage.value, attempt, "RUNNING", utc_now(), key),
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

    def prune_terminal_jobs(self, keep: int = MAX_TERMINAL_JOBS) -> int:
        keep = min(max(int(keep), 1), MAX_TERMINAL_JOBS)
        rows = self.db.execute(
            "SELECT job_id FROM supervisor_jobs WHERE status IN (?,?,?,?) ORDER BY updated_at DESC LIMIT -1 OFFSET ?",
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
        return {
            "status": "HEALTHY", "db_reachable": True,
            "single_instance_lock": bool(lock and lock["expires_at"] > time.time()),
            "active_jobs": counts["active"], "queue_depth": counts["queued"],
            "last_completed_job": dict(last) if last else None,
            "last_error": error["last_error"] if error else None,
        }
