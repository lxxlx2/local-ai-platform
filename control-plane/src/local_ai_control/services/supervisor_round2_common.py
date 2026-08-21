from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from local_ai_control.services.security import SecretFirewall
from .supervisor_contracts import AI_ROOT, MAX_FINDINGS_PER_REVIEW, _json_exact

SENSITIVE_KEY = re.compile(r"prompt|token|secret|password|credential|cookie|authorization", re.I)
MAX_CANDIDATE_SCAN_BYTES = 1_000_000
REVIEW_RESULT_SCHEMA = {
    "type": "object",
    "required": ["status", "findings"],
    "properties": {
        "status": {"enum": ["PASS", "FAIL"]},
        "findings": {"type": "array", "maxItems": MAX_FINDINGS_PER_REVIEW},
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
            if SENSITIVE_KEY.search(name):
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
class ReviewTaskSpec:
    repo_root: Path
    allowed_paths: tuple[Path, ...]
    task_prompt: str
    read_only: bool
    risk_level: str
    timeout_seconds: float
    model_role: str
    expected_review_schema: dict

    def validate(self) -> dict:
        root = self.repo_root.resolve()
        if root != AI_ROOT.resolve():
            raise PermissionError("review repo_root denied")
        if self.read_only is not True:
            raise PermissionError("review task must be read-only")
        if self.model_role != "REVIEW":
            raise ValueError("review task model_role must be REVIEW")
        allowed = []
        for path in self.allowed_paths:
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise PermissionError("review allowed_path traversal denied")
            allowed.append(str(resolved))
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
        return {
            "repo_root": str(root), "allowed_paths": allowed, "read_only": True,
            "risk_level": self.risk_level, "timeout_seconds": float(self.timeout_seconds),
            "model_role": "REVIEW", "expected_review_schema": schema,
            "task_prompt_sha256": hashlib.sha256(self.task_prompt.encode()).hexdigest(),
        }


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
    created_at: str
    status: str


@dataclass(frozen=True)
class PersistedReviewSubmission:
    review_work_unit_id: str
    job_id: str
    owner_id: str
    review_round: int
    result_hash: str
    created_at: str
    status: str
