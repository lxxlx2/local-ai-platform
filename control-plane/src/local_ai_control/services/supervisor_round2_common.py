from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from local_ai_control.services.security import SecretFirewall
from .supervisor_contracts import (
    AI_ROOT, MAX_FINDINGS_PER_REVIEW, CandidateIdentity, RepoAccessPolicy, _json_exact,
)

SENSITIVE_KEY = re.compile(r"prompt|token|secret|password|credential|cookie|authorization", re.I)
MAX_CANDIDATE_SCAN_BYTES = 1_000_000
REVIEW_RESULT_SCHEMA = {
    "type": "object",
    "required": ["status", "findings"],
    "properties": {
        "status": {"enum": ["PASS", "FAIL"]},
        "findings": {
            "type": "array", "maxItems": MAX_FINDINGS_PER_REVIEW,
            "items": {
                "type": "object", "required": ["scope", "severity", "evidence", "recommended_fix"],
                "properties": {
                    "scope": {"enum": ["FILE", "WORKFLOW"]},
                    "severity": {"enum": ["BLOCKING", "HIGH", "MEDIUM", "LOW"]},
                    "file": {"type": ["string", "null"]},
                    "evidence": {"type": "string"},
                    "recommended_fix": {"type": "string"},
                },
            },
        },
    },
}


def _canonical_digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def recursive_private_sanitize(value):
    if isinstance(value, Mapping):
        clean = {}
        for key, item in value.items():
            name = str(key)
            if SENSITIVE_KEY.search(name) and not name.endswith("_sha256"):
                clean[f"{name}_sha256"] = _canonical_digest(item)
            else:
                clean[name] = recursive_private_sanitize(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [recursive_private_sanitize(item) for item in value]
    if isinstance(value, str) and SecretFirewall().inspect(value).action == "BLOCK":
        return {"redacted": True, "sha256": hashlib.sha256(value.encode()).hexdigest()}
    return value


@dataclass(frozen=True)
class TaskObjective:
    goal: str
    acceptance_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    source_work_unit_id: str | None = None

    def to_mapping(self) -> dict:
        values = {
            "goal": self.goal,
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "expected_artifacts": list(self.expected_artifacts),
            "source_work_unit_id": self.source_work_unit_id,
        }
        _json_exact(values, 256_000)
        if not self.goal or SecretFirewall().inspect(self.goal).action == "BLOCK":
            raise ValueError("task objective goal is empty or secret-bearing")
        for collection in (self.acceptance_criteria, self.constraints, self.expected_artifacts):
            if len(collection) > 100 or any(not item or SecretFirewall().inspect(item).action == "BLOCK"
                                             for item in collection):
                raise ValueError("task objective collection is invalid")
        if self.source_work_unit_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.source_work_unit_id):
            raise ValueError("task objective source work unit is invalid")
        return values

    @classmethod
    def from_mapping(cls, value: Mapping) -> "TaskObjective":
        return cls(
            str(value.get("goal", "")),
            tuple(str(item) for item in value.get("acceptance_criteria", ())),
            tuple(str(item) for item in value.get("constraints", ())),
            tuple(str(item) for item in value.get("expected_artifacts", ())),
            str(value["source_work_unit_id"]) if value.get("source_work_unit_id") else None,
        )


@dataclass(frozen=True)
class ReviewTaskSpec:
    repo_root: Path
    allowed_paths: tuple[Path, ...]
    task_prompt: str
    read_only: bool
    risk_level: str
    timeout_seconds: float
    model_role: str
    expected_review_schema: dict
    safe_file_manifest: tuple[dict, ...] = ()
    candidate_identity: CandidateIdentity | None = None
    task_objective: TaskObjective | None = None
    objective_sha256: str | None = None
    objective_manifest_hash: str | None = None

    def validate(self) -> dict:
        root = self.repo_root.resolve()
        if root != AI_ROOT.resolve():
            raise PermissionError("review repo_root denied")
        if self.read_only is not True:
            raise PermissionError("review task must be read-only")
        if self.model_role != "REVIEW":
            raise ValueError("review task model_role must be REVIEW")
        policy = RepoAccessPolicy(root)
        allowed = [str(path) for path in policy.validate_allowed_paths(list(self.allowed_paths))]
        generated_manifest = policy.build_safe_file_manifest(tuple(Path(path) for path in allowed))
        manifest = (policy.validate_supplied_manifest(self.safe_file_manifest,
                    tuple(Path(path) for path in allowed), self.candidate_identity)
                    if self.safe_file_manifest else generated_manifest)
        if not self.task_prompt or len(self.task_prompt.encode()) > 256_000:
            raise ValueError("review prompt outside safe size bound")
        if SecretFirewall().inspect(self.task_prompt).action == "BLOCK":
            raise ValueError("review prompt rejected by Secret Firewall")
        if not 1 <= float(self.timeout_seconds) <= 3600:
            raise ValueError("review timeout outside safe range")
        schema = recursive_private_sanitize(self.expected_review_schema)
        _json_exact(schema, 16_000)
        if _canonical_digest(schema) != _canonical_digest(REVIEW_RESULT_SCHEMA):
            raise ValueError("unsupported review result schema")
        objective_mapping = self.task_objective.to_mapping() if self.task_objective else None
        if objective_mapping is not None:
            objective_sha = hashlib.sha256(_json_exact(objective_mapping, 256_000).encode()).hexdigest()
            if self.objective_sha256 != objective_sha:
                raise ValueError("review objective content hash mismatch")
            if not self.objective_manifest_hash or not re.fullmatch(r"[a-f0-9]{64}", self.objective_manifest_hash):
                raise ValueError("review objective manifest hash missing")
        return {
            "repo_root": str(root), "allowed_paths": allowed, "read_only": True,
            "risk_level": self.risk_level, "timeout_seconds": float(self.timeout_seconds),
            "model_role": "REVIEW", "expected_review_schema": schema,
            "task_prompt_sha256": hashlib.sha256(self.task_prompt.encode()).hexdigest(),
            "safe_file_manifest": list(manifest),
            "task_objective": objective_mapping,
            "objective_sha256": self.objective_sha256,
            "objective_manifest_hash": self.objective_manifest_hash,
        }

    def read_safe_file(self, value: str) -> bytes:
        validated = self.validate()
        return RepoAccessPolicy(self.repo_root).read_safe_file(
            value, self.allowed_paths, tuple(validated["safe_file_manifest"]),
        )

    def execution_view(self) -> "ReviewTaskSpec":
        validated = self.validate()
        file_paths = tuple(self.repo_root / item["path"] for item in validated["safe_file_manifest"])
        if not file_paths:
            raise PermissionError("safe reviewer manifest contains no files")
        return ReviewTaskSpec(
            self.repo_root, file_paths, self.task_prompt, self.read_only, self.risk_level,
            self.timeout_seconds, self.model_role, self.expected_review_schema,
            tuple(validated["safe_file_manifest"]), self.candidate_identity,
            self.task_objective, self.objective_sha256, self.objective_manifest_hash,
        )


@dataclass(frozen=True)
class ReviewerWorkUnit:
    review_work_unit_id: str
    job_id: str
    owner_id: str
    review_round: int
    repo_root: Path
    allowed_paths: tuple[Path, ...]
    read_only: bool
    risk_level: str
    timeout_seconds: float
    model_role: str
    expected_review_schema: dict
    prompt_content_ref: str
    prompt_sha256: str
    spec_hash: str
    candidate_identity: CandidateIdentity
    created_at: str
    status: str
    safe_file_manifest: tuple[dict, ...] = ()
    patch_content_ref: str | None = None
    patch_sha256: str | None = None
    candidate_identity_sha256: str | None = None
    objective_content_ref: str | None = None
    objective_sha256: str | None = None
    objective_manifest_hash: str | None = None


@dataclass(frozen=True)
class PersistedReviewSubmission:
    review_work_unit_id: str
    job_id: str
    owner_id: str
    review_round: int
    result_hash: str
    created_at: str
    status: str
