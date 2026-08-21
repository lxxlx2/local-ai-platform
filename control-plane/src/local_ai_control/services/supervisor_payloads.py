from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Sequence

from .supervisor_contracts import (
    AI_ROOT, MAX_FINDINGS_PER_JOB, MAX_FINDINGS_PER_REVIEW, CodexTaskSpec, PersistedReviewFinding,
    ReviewFinding, WorkUnitSpec, WorkflowStage, _json_exact, _normalize_relative_path, _safe_review_text,
    _safe_text, utc_now,
)


class DurablePayloadMixin:
    def create_work_unit(self, job_id: str, owner_id: str, stage: WorkflowStage,
                         spec: CodexTaskSpec, work_unit_id: str | None = None,
                         review_round: int | None = None) -> WorkUnitSpec:
        job = self.get_job_for_owner(job_id, owner_id)
        if stage not in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}:
            raise ValueError("work units are restricted to mutating Codex stages")
        validated = spec.validate()
        round_number = int(job.review_round if review_round is None else review_round)
        if stage is WorkflowStage.PRODUCER:
            round_number = 0
        if round_number < 0 or round_number > job.max_review_rounds:
            raise ValueError("work unit review round outside safe range")
        identifier = work_unit_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", identifier):
            raise ValueError("invalid work_unit_id")
        existing = self.db.execute("SELECT * FROM supervisor_work_units WHERE work_unit_id=?", (identifier,)).fetchone()
        if existing:
            return self._work_unit_from_row_checked(existing, job_id, owner_id)
        content_ref, prompt_sha = self.content_store.put(identifier, spec.task_prompt)
        created = utc_now()
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO supervisor_work_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, job_id, str(owner_id), stage.value, round_number, validated["repo_root"],
                     _json_exact(validated["allowed_paths"], 16_000), validated["risk_level"],
                     validated["timeout_seconds"], validated["model_role"],
                     _json_exact(validated["expected_output_schema"], 16_000), content_ref, prompt_sha,
                     created, "READY"),
                )
        except Exception:
            self.content_store.delete(content_ref)
            raise
        return self.get_work_unit(identifier, job_id, owner_id)

    def _work_unit_from_row_checked(self, row, job_id: str, owner_id: str) -> WorkUnitSpec:
        if row["job_id"] != job_id or row["owner_id"] != str(owner_id):
            raise PermissionError("work unit owner/job mismatch")
        return WorkUnitSpec(
            row["work_unit_id"], row["job_id"], WorkflowStage(row["stage"]), Path(row["repo_root"]),
            tuple(Path(value) for value in json.loads(row["allowed_paths_json"])), row["risk_level"],
            float(row["timeout_seconds"]), row["model_role"], json.loads(row["expected_output_schema_json"]),
            row["prompt_content_ref"], row["prompt_sha256"], row["created_at"], row["status"],
            int(row["review_round"]),
        )

    def get_work_unit(self, work_unit_id: str, job_id: str, owner_id: str) -> WorkUnitSpec:
        row = self.db.execute("SELECT * FROM supervisor_work_units WHERE work_unit_id=?", (work_unit_id,)).fetchone()
        if not row:
            raise KeyError("work unit not found")
        return self._work_unit_from_row_checked(row, job_id, owner_id)

    def work_unit_for_stage(self, job_id: str, owner_id: str, stage: WorkflowStage,
                            review_round: int | None = None) -> WorkUnitSpec:
        job = self.get_job_for_owner(job_id, owner_id)
        round_number = 0 if stage is WorkflowStage.PRODUCER else int(job.review_round if review_round is None else review_round)
        row = self.db.execute(
            "SELECT * FROM supervisor_work_units WHERE job_id=? AND owner_id=? AND stage=? AND review_round=?",
            (job_id, str(owner_id), stage.value, round_number),
        ).fetchone()
        if not row:
            raise KeyError("durable work unit not configured")
        return self._work_unit_from_row_checked(row, job_id, owner_id)

    def load_work_unit_prompt(self, work_unit_id: str, job_id: str, owner_id: str) -> str:
        work_unit = self.get_work_unit(work_unit_id, job_id, owner_id)
        if work_unit.status == "DELETED":
            raise KeyError("work unit prompt deleted")
        return self.content_store.get(work_unit.prompt_content_ref, work_unit.prompt_sha256)

    def reconstruct_codex_task(self, job_id: str, owner_id: str, stage: WorkflowStage,
                               review_round: int | None = None) -> CodexTaskSpec:
        unit = self.work_unit_for_stage(job_id, owner_id, stage, review_round)
        prompt = self.load_work_unit_prompt(unit.work_unit_id, job_id, owner_id)
        spec = CodexTaskSpec(unit.repo_root, unit.allowed_paths, prompt, unit.risk_level,
                            unit.timeout_seconds, unit.model_role, unit.expected_output_schema)
        persisted = spec.validate()
        if persisted["task_prompt_sha256"] != unit.prompt_sha256:
            raise ValueError("work unit prompt hash mismatch")
        return spec

    def delete_work_unit_prompt(self, work_unit_id: str, job_id: str, owner_id: str) -> None:
        unit = self.get_work_unit(work_unit_id, job_id, owner_id)
        self.content_store.delete(unit.prompt_content_ref)
        with self.db:
            self.db.execute("UPDATE supervisor_work_units SET status='DELETED' WHERE work_unit_id=?", (work_unit_id,))

    def persist_review_findings(self, job_id: str, owner_id: str, review_round: int,
                                findings: Sequence[ReviewFinding], repo_root: Path = AI_ROOT) -> list[PersistedReviewFinding]:
        job = self.get_job_for_owner(job_id, owner_id)
        if not 1 <= int(review_round) <= job.max_review_rounds:
            raise ValueError("review round outside safe range")
        if len(findings) > MAX_FINDINGS_PER_REVIEW:
            raise ValueError("review finding count exceeds per-round bound")
        existing_count = self.db.execute(
            "SELECT COUNT(*) FROM supervisor_review_findings WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        root = Path(repo_root).resolve()
        prepared = []
        for finding in findings:
            if finding.severity not in {"BLOCKING", "HIGH", "MEDIUM", "LOW"}:
                raise ValueError("invalid review severity")
            path = _normalize_relative_path(finding.file, root, "review finding")
            evidence = _safe_review_text(finding.evidence)
            recommended = _safe_review_text(finding.recommended_fix)
            basis = _json_exact({
                "job_id": job_id, "review_round": int(review_round), "severity": finding.severity,
                "file": path, "evidence": evidence, "recommended_fix": recommended,
            }, 16_000)
            finding_id = hashlib.sha256(basis.encode()).hexdigest()[:40]
            created = utc_now()
            integrity = hashlib.sha256(_json_exact({
                "finding_id": finding_id, "job_id": job_id, "review_round": int(review_round),
                "severity": finding.severity, "file": path, "evidence": evidence,
                "recommended_fix": recommended, "created_at": created,
            }, 20_000).encode()).hexdigest()
            prepared.append((finding_id, job_id, str(owner_id), int(review_round), finding.severity,
                             path, evidence, recommended, created, integrity, "NEW", None))
        unique_new = {item[0] for item in prepared}
        already = 0
        if unique_new:
            placeholders = ",".join("?" for _ in unique_new)
            already = self.db.execute(
                f"SELECT COUNT(*) FROM supervisor_review_findings WHERE finding_id IN ({placeholders})",
                tuple(unique_new),
            ).fetchone()[0]
        if existing_count + len(unique_new) - already > MAX_FINDINGS_PER_JOB:
            raise ValueError("review finding history exceeds per-job bound")
        with self.db:
            for row in prepared:
                self.db.execute(
                    "INSERT OR IGNORE INTO supervisor_review_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", row
                )
        return self.review_findings(job_id, owner_id, int(review_round))

    def _finding_integrity(self, row) -> str:
        return hashlib.sha256(_json_exact({
            "finding_id": row["finding_id"], "job_id": row["job_id"],
            "review_round": int(row["review_round"]), "severity": row["severity"],
            "file": row["file_path"], "evidence": row["evidence_summary"],
            "recommended_fix": row["recommended_fix"], "created_at": row["created_at"],
        }, 20_000).encode()).hexdigest()

    def review_findings(self, job_id: str, owner_id: str, review_round: int) -> list[PersistedReviewFinding]:
        self.get_job_for_owner(job_id, owner_id)
        rows = self.db.execute(
            "SELECT * FROM supervisor_review_findings WHERE job_id=? AND owner_id=? AND review_round=? ORDER BY created_at,finding_id",
            (job_id, str(owner_id), int(review_round)),
        ).fetchall()
        results = []
        for row in rows:
            if self._finding_integrity(row) != row["integrity_hash"]:
                raise ValueError("review finding integrity mismatch")
            results.append(PersistedReviewFinding(
                row["finding_id"], row["job_id"], int(row["review_round"]), row["severity"],
                row["file_path"], row["evidence_summary"], row["recommended_fix"], row["created_at"],
                row["integrity_hash"], row["status"], row["consumed_by_revision"],
            ))
        return results

    def mark_review_findings_consumed(self, job_id: str, owner_id: str, review_round: int,
                                      revision_token: str) -> int:
        self.get_job_for_owner(job_id, owner_id)
        safe_token = _safe_text(revision_token, 200) or "revision"
        with self.db:
            cursor = self.db.execute(
                "UPDATE supervisor_review_findings SET status='CONSUMED',consumed_by_revision=? "
                "WHERE job_id=? AND owner_id=? AND review_round=?",
                (safe_token, job_id, str(owner_id), int(review_round)),
            )
        return cursor.rowcount
