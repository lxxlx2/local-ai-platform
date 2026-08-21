from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Mapping

from .supervisor_contracts import (
    MAX_FINDINGS_PER_REVIEW, MAX_TERMINAL_JOBS, JobStatus, ReviewFinding, ReviewResult,
    WorkflowStage, _json_exact, _normalize_relative_path, _safe_review_text, utc_now,
)
from .supervisor_round2_common import PersistedReviewSubmission, ReviewTaskSpec, ReviewerWorkUnit, _canonical_digest

class Round2ReviewRepositoryMixin:
    @staticmethod
    def _review_manifest(job_id: str, owner_id: str, review_round: int, validated: Mapping, prompt_sha: str) -> dict:
        return {
            "job_id": job_id, "owner_id": str(owner_id), "review_round": int(review_round),
            "repo_root": validated["repo_root"], "allowed_paths": sorted(validated["allowed_paths"]),
            "read_only": True, "risk_level": validated["risk_level"],
            "timeout_seconds": float(validated["timeout_seconds"]), "model_role": "REVIEW",
            "expected_review_schema": validated["expected_review_schema"], "prompt_sha256": prompt_sha,
        }

    def create_review_work_unit(self, job_id: str, owner_id: str, review_round: int,
                                spec: ReviewTaskSpec, review_work_unit_id: str | None = None) -> ReviewerWorkUnit:
        job = self.get_job_for_owner(job_id, owner_id)
        round_number = int(review_round)
        if not 1 <= round_number <= job.max_review_rounds:
            raise ValueError("review work unit round outside safe range")
        validated = spec.validate()
        identifier = review_work_unit_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", identifier):
            raise ValueError("invalid review_work_unit_id")
        prompt_sha = validated["task_prompt_sha256"]
        spec_hash = _canonical_digest(self._review_manifest(job_id, owner_id, round_number, validated, prompt_sha))
        existing = self.db.execute(
            "SELECT * FROM supervisor_review_work_units WHERE review_work_unit_id=?", (identifier,)
        ).fetchone()
        if existing:
            unit = self.get_review_work_unit(identifier, job_id, owner_id, round_number)
            if unit.spec_hash != spec_hash:
                raise ValueError("review work unit id conflicts with immutable manifest")
            return unit
        content_ref, stored_sha = self.content_store.put(f"review-{identifier}", spec.task_prompt)
        if stored_sha != prompt_sha:
            self.content_store.delete(content_ref)
            raise ValueError("review prompt integrity mismatch")
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO supervisor_review_work_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, job_id, str(owner_id), round_number, validated["repo_root"],
                     _json_exact(validated["allowed_paths"], 16_000), 1, validated["risk_level"],
                     validated["timeout_seconds"], "REVIEW", _json_exact(validated["expected_review_schema"], 16_000),
                     content_ref, prompt_sha, spec_hash, utc_now(), "READY"),
                )
        except Exception:
            self.content_store.delete(content_ref)
            raise
        return self.get_review_work_unit(identifier, job_id, owner_id, round_number)

    def get_review_work_unit(self, review_work_unit_id: str, job_id: str, owner_id: str,
                             review_round: int) -> ReviewerWorkUnit:
        row = self.db.execute(
            "SELECT * FROM supervisor_review_work_units WHERE review_work_unit_id=?", (review_work_unit_id,)
        ).fetchone()
        if not row:
            raise KeyError("review work unit not found")
        if row["job_id"] != job_id or row["owner_id"] != str(owner_id) or int(row["review_round"]) != int(review_round):
            raise PermissionError("review work unit owner/job/round mismatch")
        validated = {
            "repo_root": row["repo_root"], "allowed_paths": json.loads(row["allowed_paths_json"]),
            "read_only": bool(row["read_only"]), "risk_level": row["risk_level"],
            "timeout_seconds": float(row["timeout_seconds"]), "model_role": row["model_role"],
            "expected_review_schema": json.loads(row["expected_review_schema_json"]),
        }
        expected = _canonical_digest(self._review_manifest(job_id, owner_id, int(review_round), validated, row["prompt_sha256"]))
        if row["spec_hash"] != expected or not bool(row["read_only"]) or row["model_role"] != "REVIEW":
            raise ValueError("review work unit integrity mismatch")
        return ReviewerWorkUnit(
            row["review_work_unit_id"], row["job_id"], row["owner_id"], int(row["review_round"]),
            Path(row["repo_root"]), tuple(Path(x) for x in json.loads(row["allowed_paths_json"])),
            True, row["risk_level"], float(row["timeout_seconds"]), row["model_role"],
            json.loads(row["expected_review_schema_json"]), row["prompt_content_ref"], row["prompt_sha256"],
            row["spec_hash"], row["created_at"], row["status"],
        )

    def review_work_unit_for_round(self, job_id: str, owner_id: str, review_round: int) -> ReviewerWorkUnit:
        self.get_job_for_owner(job_id, owner_id)
        row = self.db.execute(
            "SELECT review_work_unit_id FROM supervisor_review_work_units WHERE job_id=? AND owner_id=? AND review_round=?",
            (job_id, str(owner_id), int(review_round)),
        ).fetchone()
        if not row:
            raise KeyError("durable review work unit not configured")
        return self.get_review_work_unit(row["review_work_unit_id"], job_id, owner_id, review_round)

    def reconstruct_reviewer_task(self, job_id: str, owner_id: str, review_round: int) -> ReviewTaskSpec:
        unit = self.review_work_unit_for_round(job_id, owner_id, review_round)
        prompt = self.content_store.get(unit.prompt_content_ref, unit.prompt_sha256)
        spec = ReviewTaskSpec(unit.repo_root, unit.allowed_paths, prompt, unit.read_only, unit.risk_level,
                              unit.timeout_seconds, unit.model_role, unit.expected_review_schema)
        validated = spec.validate()
        if validated["task_prompt_sha256"] != unit.prompt_sha256:
            raise ValueError("review work unit prompt hash mismatch")
        return spec

    @staticmethod
    def _normalized_result(result: ReviewResult, repo_root: Path) -> tuple[dict, ReviewResult]:
        if result.status not in {"PASS", "FAIL"}:
            raise ValueError("review status must be PASS or FAIL")
        if result.status == "PASS" and result.findings:
            raise ValueError("PASS review cannot contain findings")
        if result.status == "FAIL" and not result.findings:
            raise ValueError("FAIL review must contain findings")
        if len(result.findings) > MAX_FINDINGS_PER_REVIEW:
            raise ValueError("review finding count exceeds bound")
        normalized_findings = []
        rebuilt = []
        root = Path(repo_root).resolve()
        for finding in result.findings:
            if finding.severity not in {"BLOCKING", "HIGH", "MEDIUM", "LOW"}:
                raise ValueError("invalid review severity")
            path = _normalize_relative_path(finding.file, root, "review finding")
            evidence = _safe_review_text(finding.evidence)
            fix = _safe_review_text(finding.recommended_fix)
            normalized_findings.append({"severity": finding.severity, "file": path,
                                        "evidence": evidence, "recommended_fix": fix})
            rebuilt.append(ReviewFinding(finding.severity, path, evidence, fix))
        return {"status": result.status, "findings": normalized_findings}, ReviewResult(result.status, tuple(rebuilt))

    def submit_review_result(self, job_id: str, owner_id: str, review_round: int,
                             review_work_unit_id: str, result: ReviewResult) -> PersistedReviewSubmission:
        unit = self.get_review_work_unit(review_work_unit_id, job_id, owner_id, review_round)
        normalized, _ = self._normalized_result(result, unit.repo_root)
        encoded = _json_exact(normalized, 128_000)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        existing = self.db.execute(
            "SELECT * FROM supervisor_review_results WHERE review_work_unit_id=?", (review_work_unit_id,)
        ).fetchone()
        if existing:
            if (existing["job_id"] != job_id or existing["owner_id"] != str(owner_id)
                    or int(existing["review_round"]) != int(review_round)):
                raise PermissionError("review result owner/job/round mismatch")
            if existing["result_hash"] != digest or existing["result_json"] != encoded:
                raise ValueError("conflicting durable review result")
            return PersistedReviewSubmission(review_work_unit_id, job_id, str(owner_id), int(review_round),
                                             digest, existing["created_at"], existing["status"])
        created = utc_now()
        with self.db:
            self.db.execute(
                "INSERT INTO supervisor_review_results VALUES(?,?,?,?,?,?,?,?)",
                (review_work_unit_id, job_id, str(owner_id), int(review_round), encoded, digest, created, "SUBMITTED"),
            )
            self.db.execute(
                "UPDATE supervisor_review_work_units SET status='RESULT_SUBMITTED' WHERE review_work_unit_id=?",
                (review_work_unit_id,),
            )
            job = self.get_job(job_id)
            if (job.owner_id == str(owner_id) and job.current_stage is WorkflowStage.REVIEW
                    and job.status is JobStatus.WAITING and job.resume_state == "REVIEW_RESULT_PENDING"):
                self.db.execute(
                    "UPDATE supervisor_jobs SET status=?,resume_state=NULL,updated_at=? WHERE job_id=?",
                    (JobStatus.QUEUED.value, utc_now(), job_id),
                )
        return PersistedReviewSubmission(review_work_unit_id, job_id, str(owner_id), int(review_round), digest, created, "SUBMITTED")

    def submitted_review_result(self, job_id: str, owner_id: str, review_round: int,
                                review_work_unit_id: str) -> ReviewResult:
        unit = self.get_review_work_unit(review_work_unit_id, job_id, owner_id, review_round)
        row = self.db.execute(
            "SELECT * FROM supervisor_review_results WHERE review_work_unit_id=?", (review_work_unit_id,)
        ).fetchone()
        if not row:
            raise KeyError("durable review result not submitted")
        if row["job_id"] != job_id or row["owner_id"] != str(owner_id) or int(row["review_round"]) != int(review_round):
            raise PermissionError("review result owner/job/round mismatch")
        if hashlib.sha256(row["result_json"].encode()).hexdigest() != row["result_hash"]:
            raise ValueError("durable review result integrity mismatch")
        payload = json.loads(row["result_json"])
        findings = tuple(ReviewFinding(item["severity"], item["file"], item["evidence"], item["recommended_fix"])
                         for item in payload["findings"])
        _, result = self._normalized_result(ReviewResult(payload["status"], findings), unit.repo_root)
        return result

    def mark_review_result_consumed(self, job_id: str, owner_id: str, review_round: int,
                                    review_work_unit_id: str) -> None:
        self.get_review_work_unit(review_work_unit_id, job_id, owner_id, review_round)
        token = getattr(self, "active_lease_token", None)
        if getattr(self, "lease_failed", False):
            from .supervisor_contracts import LeaseLostError
            raise LeaseLostError("lease keeper failed; review result consumption denied")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if token is not None and not self._lease_row_owned(token):
                from .supervisor_contracts import LeaseLostError
                self.db.rollback()
                raise LeaseLostError("lease ownership required for review result consumption")
            row = self.db.execute(
                "SELECT status FROM supervisor_review_results WHERE review_work_unit_id=?",
                (review_work_unit_id,),
            ).fetchone()
            if not row:
                self.db.rollback()
                raise KeyError("durable review result not submitted")
            if row["status"] not in {"SUBMITTED", "CONSUMED"}:
                self.db.rollback()
                raise ValueError("invalid durable review result status")
            self.db.execute(
                "UPDATE supervisor_review_results SET status='CONSUMED' WHERE review_work_unit_id=?",
                (review_work_unit_id,),
            )
            self.db.execute(
                "UPDATE supervisor_review_work_units SET status='CONSUMED' WHERE review_work_unit_id=?",
                (review_work_unit_id,),
            )
            self.db.commit()
        except Exception:
            if self.db.in_transaction:
                self.db.rollback()
            raise

    def has_submitted_review_result(self, job_id: str, owner_id: str, review_round: int) -> bool:
        unit = self.review_work_unit_for_round(job_id, owner_id, review_round)
        row = self.db.execute(
            "SELECT 1 FROM supervisor_review_results WHERE review_work_unit_id=?", (unit.review_work_unit_id,)
        ).fetchone()
        return bool(row)

    def prune_terminal_jobs(self, keep: int = MAX_TERMINAL_JOBS) -> int:
        keep = min(max(int(keep), 1), MAX_TERMINAL_JOBS)
        rows = self.db.execute(
            "SELECT job_id FROM supervisor_jobs WHERE status IN (?,?,?,?) ORDER BY updated_at DESC LIMIT -1 OFFSET ?",
            (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELED.value,
             JobStatus.BLOCKED.value, keep),
        ).fetchall()
        job_ids = [row["job_id"] for row in rows]
        with self.db:
            for job_id in job_ids:
                units = self.db.execute(
                    "SELECT prompt_content_ref FROM supervisor_review_work_units WHERE job_id=?", (job_id,)
                ).fetchall()
                for unit in units:
                    self.content_store.delete(unit["prompt_content_ref"])
                self.db.execute("DELETE FROM supervisor_review_results WHERE job_id=?", (job_id,))
                self.db.execute("DELETE FROM supervisor_review_work_units WHERE job_id=?", (job_id,))
        return super().prune_terminal_jobs(keep)
