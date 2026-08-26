from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from .generic_project_policy import GenericRepoAccessPolicy
from .supervisor_contracts import (
    CodexTaskSpec,
    WorkUnitSpec,
    WorkflowStage,
    _json_exact,
    utc_now,
)
from .supervisor_generic_project import GenericProjectSupervisorRepository
from .supervisor_round2_common import _canonical_digest
from .supervisor_round2_common import ReviewerWorkUnit


class GuardedGenericProjectSupervisorRepository(GenericProjectSupervisorRepository):
    """Generic-project repository with generic access policy at every manifest boundary.

    The shared Round2 repository is intentionally scoped to the local-ai-platform
    source/docs roots. Generic projects authorize one exact isolated Git worktree,
    so Review and Revision manifest construction must use GenericRepoAccessPolicy
    rather than the shared RepoAccessPolicy defaults.
    """

    def create_work_unit(
        self,
        job_id: str,
        owner_id: str,
        stage: WorkflowStage,
        spec: CodexTaskSpec,
        work_unit_id: str | None = None,
        review_round: int | None = None,
    ) -> WorkUnitSpec:
        if stage is not WorkflowStage.REVISION:
            return super().create_work_unit(
                job_id,
                owner_id,
                stage,
                spec,
                work_unit_id=work_unit_id,
                review_round=review_round,
            )

        job = self.get_job_for_owner(job_id, owner_id)
        validated = spec.validate()
        round_number = int(job.review_round if review_round is None else review_round)
        if round_number < 0 or round_number > job.max_review_rounds:
            raise ValueError("work unit review round outside safe range")
        if not job.mutation_capable:
            raise PermissionError("read-only job cannot create mutating work unit")
        if not job.baseline_commit_sha:
            raise ValueError("trusted immutable job baseline is missing")

        identity = self.candidate_identity_provider.snapshot(job.baseline_commit_sha)
        validated["candidate_identity"] = identity.to_mapping()
        policy = GenericRepoAccessPolicy(Path(validated["repo_root"]))
        validated["safe_file_manifest"] = list(
            policy.merge_candidate_manifest(
                identity,
                tuple(Path(path) for path in validated["allowed_paths"]),
            )
        )

        identifier = work_unit_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", identifier):
            raise ValueError("invalid work_unit_id")
        prompt_sha = validated["task_prompt_sha256"]
        spec_hash = self._work_spec_hash(
            job_id,
            owner_id,
            stage,
            round_number,
            validated,
            prompt_sha,
        )
        existing = self.db.execute(
            "SELECT * FROM supervisor_work_units WHERE work_unit_id=?",
            (identifier,),
        ).fetchone()
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
                    "timeout_seconds,model_role,expected_output_schema_json,prompt_content_ref,prompt_sha256,created_at,"
                    "status,spec_hash,safe_file_manifest_json,candidate_identity_json,write_roots_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        job_id,
                        str(owner_id),
                        stage.value,
                        round_number,
                        validated["repo_root"],
                        _json_exact(validated["allowed_paths"], 16_000),
                        validated["risk_level"],
                        validated["timeout_seconds"],
                        validated["model_role"],
                        _json_exact(validated["expected_output_schema"], 16_000),
                        content_ref,
                        prompt_sha,
                        utc_now(),
                        "READY",
                        spec_hash,
                        _json_exact(validated["safe_file_manifest"], 1_000_000),
                        _json_exact(validated["candidate_identity"], 64_000),
                        _json_exact(validated["write_roots"], 16_000),
                    ),
                )
        except Exception:
            self.content_store.delete(content_ref)
            raise
        return self.get_work_unit(identifier, job_id, owner_id)

    def create_review_work_unit(
        self,
        job_id: str,
        owner_id: str,
        review_round: int,
        spec,
        review_work_unit_id: str | None = None,
    ) -> ReviewerWorkUnit:
        job = self.get_job_for_owner(job_id, owner_id)
        round_number = int(review_round)
        if job.current_stage is not WorkflowStage.REVIEW:
            raise ValueError("review work unit requires REVIEW stage")
        if round_number != job.review_round + 1 or not 1 <= round_number <= job.max_review_rounds:
            raise ValueError("review work unit round outside safe range")

        validated = spec.validate()
        identifier = review_work_unit_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", identifier):
            raise ValueError("invalid review_work_unit_id")
        prompt_sha = validated["task_prompt_sha256"]
        if not job.baseline_commit_sha:
            raise ValueError("trusted immutable job baseline is missing")

        candidate_identity = self.candidate_identity_provider.snapshot(job.baseline_commit_sha)
        policy = GenericRepoAccessPolicy(Path(validated["repo_root"]))
        validated["safe_file_manifest"] = list(
            policy.merge_candidate_manifest(
                candidate_identity,
                tuple(Path(path) for path in validated["allowed_paths"]),
            )
        )
        patch = self.candidate_identity_provider.build_review_patch(candidate_identity)
        objective, objective_sha, objective_manifest_hash = self._objective_for_review(job_id, owner_id)
        validated["objective_sha256"] = objective_sha
        validated["objective_manifest_hash"] = objective_manifest_hash
        candidate_identity_sha256 = _canonical_digest(candidate_identity.stable_payload())
        patch_sha256 = hashlib.sha256(patch.encode()).hexdigest()
        spec_hash = _canonical_digest(
            self._review_manifest(
                job_id,
                owner_id,
                round_number,
                validated,
                prompt_sha,
                candidate_identity,
                patch_sha256,
                candidate_identity_sha256,
            )
        )

        existing = self.db.execute(
            "SELECT * FROM supervisor_review_work_units WHERE review_work_unit_id=?",
            (identifier,),
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
        patch_ref = None
        objective_ref = None
        try:
            objective_ref, stored_objective_sha = self.content_store.put(
                f"review-{identifier}-objective",
                _json_exact(objective.to_mapping(), 256_000),
            )
            if stored_objective_sha != objective_sha:
                raise ValueError("review objective integrity mismatch")
            patch_ref, stored_patch_sha = self.content_store.put(
                f"review-{identifier}-patch",
                patch,
            )
            if stored_patch_sha != patch_sha256:
                raise ValueError("review patch integrity mismatch")
            with self.db:
                self.db.execute(
                    "INSERT INTO supervisor_review_work_units "
                    "(review_work_unit_id,job_id,owner_id,review_round,repo_root,allowed_paths_json,read_only,"
                    "risk_level,timeout_seconds,model_role,expected_review_schema_json,prompt_content_ref,"
                    "prompt_sha256,spec_hash,candidate_identity_json,safe_file_manifest_json,"
                    "patch_content_ref,patch_sha256,candidate_identity_sha256,"
                    "objective_content_ref,objective_sha256,objective_manifest_hash,created_at,status) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        job_id,
                        str(owner_id),
                        round_number,
                        validated["repo_root"],
                        _json_exact(validated["allowed_paths"], 16_000),
                        1,
                        validated["risk_level"],
                        validated["timeout_seconds"],
                        "REVIEW",
                        _json_exact(validated["expected_review_schema"], 16_000),
                        content_ref,
                        prompt_sha,
                        spec_hash,
                        _json_exact(candidate_identity.to_mapping(), 64_000),
                        _json_exact(validated["safe_file_manifest"], 1_000_000),
                        patch_ref,
                        patch_sha256,
                        candidate_identity_sha256,
                        objective_ref,
                        objective_sha,
                        objective_manifest_hash,
                        utc_now(),
                        "READY",
                    ),
                )
        except Exception:
            self.content_store.delete(content_ref)
            if patch_ref:
                self.content_store.delete(patch_ref)
            if objective_ref:
                self.content_store.delete(objective_ref)
            raise
        return self.get_review_work_unit(identifier, job_id, owner_id, round_number)
