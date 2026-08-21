from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Mapping

from .supervisor_contracts import (
    MAX_EVENTS_PER_JOB, CodexTaskSpec, JobStatus, LeaseLostError, StageResult, WorkUnitSpec,
    WorkflowJob, WorkflowStage, _json_exact, _safe_json, _safe_text, utc_now,
)
from .supervisor_repository import SupervisorRepository as BaseSupervisorRepository
from .supervisor_round2_common import SENSITIVE_KEY, _canonical_digest, recursive_private_sanitize

class Round2RepositoryCoreMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_lease_token: str | None = None
        self.lease_failed = False

    def migrate(self) -> None:
        super().migrate()
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(supervisor_work_units)").fetchall()}
        if "spec_hash" not in columns:
            with self.db:
                self.db.execute("ALTER TABLE supervisor_work_units ADD COLUMN spec_hash TEXT")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS supervisor_review_work_units(
              review_work_unit_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner_id TEXT NOT NULL,
              review_round INTEGER NOT NULL, repo_root TEXT NOT NULL, allowed_paths_json TEXT NOT NULL,
              read_only INTEGER NOT NULL, risk_level TEXT NOT NULL, timeout_seconds REAL NOT NULL,
              model_role TEXT NOT NULL, expected_review_schema_json TEXT NOT NULL,
              prompt_content_ref TEXT NOT NULL, prompt_sha256 TEXT NOT NULL, spec_hash TEXT NOT NULL,
              created_at TEXT NOT NULL, status TEXT NOT NULL,
              FOREIGN KEY(job_id) REFERENCES supervisor_jobs(job_id),
              UNIQUE(job_id, review_round)
            );
            CREATE TABLE IF NOT EXISTS supervisor_review_results(
              review_work_unit_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner_id TEXT NOT NULL,
              review_round INTEGER NOT NULL, result_json TEXT NOT NULL, result_hash TEXT NOT NULL,
              created_at TEXT NOT NULL, status TEXT NOT NULL,
              FOREIGN KEY(review_work_unit_id) REFERENCES supervisor_review_work_units(review_work_unit_id),
              UNIQUE(job_id, review_round)
            );
            """
        )
        self.db.commit()
        self._backfill_work_unit_hashes()

    @staticmethod
    def _work_manifest(job_id: str, owner_id: str, stage: WorkflowStage, review_round: int,
                       validated: Mapping, prompt_sha: str) -> dict:
        return {
            "job_id": job_id, "owner_id": str(owner_id), "stage": stage.value,
            "review_round": int(review_round), "repo_root": validated["repo_root"],
            "allowed_paths": sorted(validated["allowed_paths"]), "risk_level": validated["risk_level"],
            "timeout_seconds": float(validated["timeout_seconds"]), "model_role": validated["model_role"],
            "expected_output_schema": validated["expected_output_schema"], "prompt_sha256": prompt_sha,
        }

    @classmethod
    def _work_spec_hash(cls, job_id, owner_id, stage, review_round, validated, prompt_sha) -> str:
        return _canonical_digest(cls._work_manifest(job_id, owner_id, stage, review_round, validated, prompt_sha))

    def _backfill_work_unit_hashes(self) -> None:
        rows = self.db.execute("SELECT * FROM supervisor_work_units WHERE spec_hash IS NULL OR spec_hash='' ").fetchall()
        with self.db:
            for row in rows:
                validated = {
                    "repo_root": row["repo_root"],
                    "allowed_paths": json.loads(row["allowed_paths_json"]),
                    "risk_level": row["risk_level"], "timeout_seconds": float(row["timeout_seconds"]),
                    "model_role": row["model_role"],
                    "expected_output_schema": json.loads(row["expected_output_schema_json"]),
                }
                digest = self._work_spec_hash(row["job_id"], row["owner_id"], WorkflowStage(row["stage"]),
                                              int(row["review_round"]), validated, row["prompt_sha256"])
                self.db.execute("UPDATE supervisor_work_units SET spec_hash=? WHERE work_unit_id=?",
                                (digest, row["work_unit_id"]))

    def create_work_unit(self, job_id: str, owner_id: str, stage: WorkflowStage,
                         spec: CodexTaskSpec, work_unit_id: str | None = None,
                         review_round: int | None = None) -> WorkUnitSpec:
        job = self.get_job_for_owner(job_id, owner_id)
        if stage not in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}:
            raise ValueError("work units are restricted to mutating Codex stages")
        validated = spec.validate()
        round_number = 0 if stage is WorkflowStage.PRODUCER else int(job.review_round if review_round is None else review_round)
        if round_number < 0 or round_number > job.max_review_rounds:
            raise ValueError("work unit review round outside safe range")
        identifier = work_unit_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", identifier):
            raise ValueError("invalid work_unit_id")
        prompt_sha = validated["task_prompt_sha256"]
        spec_hash = self._work_spec_hash(job_id, owner_id, stage, round_number, validated, prompt_sha)
        existing = self.db.execute("SELECT * FROM supervisor_work_units WHERE work_unit_id=?", (identifier,)).fetchone()
        if existing:
            existing_unit = self._work_unit_from_row_checked(existing, job_id, owner_id)
            if existing["spec_hash"] != spec_hash:
                raise ValueError("work unit id conflicts with immutable manifest")
            return existing_unit
        content_ref, stored_sha = self.content_store.put(identifier, spec.task_prompt)
        if stored_sha != prompt_sha:
            self.content_store.delete(content_ref)
            raise ValueError("work unit prompt integrity mismatch")
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO supervisor_work_units "
                    "(work_unit_id,job_id,owner_id,stage,review_round,repo_root,allowed_paths_json,risk_level,"
                    "timeout_seconds,model_role,expected_output_schema_json,prompt_content_ref,prompt_sha256,created_at,status,spec_hash) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, job_id, str(owner_id), stage.value, round_number, validated["repo_root"],
                     _json_exact(validated["allowed_paths"], 16_000), validated["risk_level"],
                     validated["timeout_seconds"], validated["model_role"],
                     _json_exact(validated["expected_output_schema"], 16_000), content_ref, prompt_sha,
                     utc_now(), "READY", spec_hash),
                )
        except Exception:
            self.content_store.delete(content_ref)
            raise
        return self.get_work_unit(identifier, job_id, owner_id)

    def _work_unit_from_row_checked(self, row, job_id: str, owner_id: str) -> WorkUnitSpec:
        unit = super()._work_unit_from_row_checked(row, job_id, owner_id)
        validated = {
            "repo_root": str(unit.repo_root.resolve()),
            "allowed_paths": [str(path.resolve()) for path in unit.allowed_paths],
            "risk_level": unit.risk_level, "timeout_seconds": float(unit.timeout_seconds),
            "model_role": unit.model_role, "expected_output_schema": unit.expected_output_schema,
        }
        expected = self._work_spec_hash(job_id, owner_id, unit.stage, unit.review_round, validated, unit.prompt_sha256)
        if not row["spec_hash"] or row["spec_hash"] != expected:
            raise ValueError("work unit immutable manifest integrity mismatch")
        return unit

    def create_job(self, *args, metadata: Mapping | None = None, **kwargs) -> WorkflowJob:
        prepared = {}
        for key, value in dict(metadata or {}).items():
            prepared[str(key)] = value if SENSITIVE_KEY.search(str(key)) else recursive_private_sanitize(value)
        return super().create_job(*args, metadata=prepared, **kwargs)

    def record_event(self, job_id: str | None, event_type: str, stage: WorkflowStage | None = None,
                     payload: Mapping | None = None, dedupe_key: str | None = None, commit=True) -> bool:
        safe_payload = recursive_private_sanitize(payload or {})
        try:
            self.db.execute(
                "INSERT INTO supervisor_events VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), job_id, event_type, stage.value if stage else None, utc_now(),
                 _safe_json(safe_payload), dedupe_key),
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

    def _lease_row_owned(self, token: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM supervisor_locks WHERE lock_name='consumer' AND owner_token=? AND expires_at>?",
            (token, time.time()),
        ).fetchone()
        return bool(row)

    def set_active_lease(self, token: str | None) -> None:
        self.active_lease_token = token
        self.lease_failed = False

    @staticmethod
    def heartbeat_external(path: Path, owner_token: str, ttl: float) -> bool:
        try:
            db = sqlite3.connect(path, timeout=2)
            now = time.time()
            with db:
                cursor = db.execute(
                    "UPDATE supervisor_locks SET heartbeat_at=?,expires_at=? "
                    "WHERE lock_name='consumer' AND owner_token=? AND expires_at>?",
                    (now, now + ttl, owner_token, now),
                )
            db.close()
            return cursor.rowcount == 1
        except sqlite3.Error:
            return False

    def update_job(self, job_id: str, **changes) -> WorkflowJob:
        token = self.active_lease_token
        if self.lease_failed:
            raise LeaseLostError("lease keeper failed; job transition denied")
        if token is None:
            return super().update_job(job_id, **changes)
        allowed = {"status", "current_stage", "attempt", "review_round", "last_error",
                   "resume_state", "next_retry_at", "metadata_json"}
        if not changes or set(changes) - allowed:
            raise ValueError("invalid job update")
        values = {key: (value.value if isinstance(value, Enum) else value) for key, value in changes.items()}
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if not self._lease_row_owned(token):
                self.db.rollback()
                raise LeaseLostError("lease ownership required for job transition")
            cursor = self.db.execute(f"UPDATE supervisor_jobs SET {assignments} WHERE job_id=?", (*values.values(), job_id))
            if cursor.rowcount != 1:
                self.db.rollback()
                raise KeyError("job not found")
            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()
            raise
        return self.get_job(job_id)

    def finish_stage(self, run_id: str, job_id: str, stage: WorkflowStage, result: StageResult) -> None:
        token = self.active_lease_token
        if self.lease_failed:
            raise LeaseLostError("lease keeper failed; stage completion denied")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if token is not None and not self._lease_row_owned(token):
                self.db.rollback()
                raise LeaseLostError("lease ownership required for stage completion")
            self.db.execute(
                "UPDATE supervisor_stage_runs SET status=?,completed_at=?,summary=?,error=?,metrics_json=? WHERE run_id=?",
                (result.status.value, utc_now(), _safe_text(result.summary), _safe_text(result.error),
                 _safe_json(recursive_private_sanitize(result.metrics), 8000), run_id),
            )
            for artifact in result.artifacts:
                safe = recursive_private_sanitize(artifact)
                self.db.execute(
                    "INSERT INTO supervisor_artifacts VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), job_id, stage.value, str(safe.get("kind", "metadata")),
                     _safe_text(str(safe.get("reference", "")), 500), safe.get("size_bytes"),
                     safe.get("sha256"), utc_now()),
                )
            self.db.commit()
        except sqlite3.Error:
            self.db.rollback()
            raise
