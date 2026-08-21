from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import platform
import random
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from local_ai_control.services.security import SecretFirewall


AI_ROOT = Path("/Users/jerson/AI")
LEARNING_RUNTIME = AI_ROOT / "runtime/learning"
LEARNING_DB = LEARNING_RUNTIME / "learning.db"
LOCAL_MODEL = AI_ROOT / "models/mlx-community/Qwen3.6-35B-A3B-4bit"
TRAINING_VENV = AI_ROOT / "runtime/training-venv"
OMLX_VENV_PYTHON = AI_ROOT / "runtime/omlx-venv/bin/python"
BASE_MODEL = "mlx-community/Qwen3.6-35B-A3B-4bit"
DATASET_NAMESPACES = {
    "personal-general", "x-content", "novel-editor", "livestream-content",
    "stickers-content", "coding-assistant",
}
QUALITY_LABELS = {
    "FACTUAL", "INSTRUCTION_FOLLOWING", "STYLE", "CODE_CORRECTNESS", "SECURITY",
    "BUSINESS_EFFECTIVENESS", "PROJECT_CONSISTENCY",
}
MAX_FIELD_BYTES = 256_000
MAX_IMPORT_BYTES = 5_000_000
MAX_IMPORT_LINES = 10_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").strip().splitlines())


def content_hash(*values: str) -> str:
    joined = "\x1f".join(canonical_text(value) for value in values)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class CandidateStatus(str, Enum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    REDACTED = "REDACTED"
    DATASET_ASSIGNED = "DATASET_ASSIGNED"
    EXPIRED = "EXPIRED"


class SourceType(str, Enum):
    TELEGRAM_OWNER_CHAT = "TELEGRAM_OWNER_CHAT"
    MANUAL_IMPORT = "MANUAL_IMPORT"
    TASK_RESULT = "TASK_RESULT"
    REVIEW_REVISION = "REVIEW_REVISION"
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"
    SYNTHETIC = "SYNTHETIC"


class FeedbackType(str, Enum):
    GOOD = "GOOD"
    BAD = "BAD"
    BETTER_RESPONSE = "BETTER_RESPONSE"
    SKIP = "SKIP"


class FormatType(str, Enum):
    SFT = "SFT"
    PREFERENCE = "PREFERENCE"
    EVAL = "EVAL"


class DatasetSplit(str, Enum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"
    GOLDEN_HOLDOUT = "GOLDEN_HOLDOUT"


class AdapterStatus(str, Enum):
    TRAINING = "TRAINING"
    CANDIDATE = "CANDIDATE"
    EVAL_FAILED = "EVAL_FAILED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"
    ARCHIVED = "ARCHIVED"


@dataclass(frozen=True)
class TrainingCandidate:
    candidate_id: str
    user_scope: str
    project_scope: str
    source_type: SourceType
    source_ref_hash: str
    prompt: str | None
    response: str | None
    feedback: str
    quality_labels: tuple[str, ...]
    privacy_labels: tuple[str, ...]
    synthetic_flag: bool
    owner_approved: bool
    created_at: str
    status: CandidateStatus
    namespace: str
    deterministic_validated: bool = False
    business_outcome_validated: bool = False


@dataclass(frozen=True)
class PreferencePair:
    pair_id: str
    prompt: str
    chosen_response: str
    rejected_response: str
    reason: str
    source_candidate_ids: tuple[str, ...]
    owner_confirmed: bool
    business_outcome_confirmed: bool


@dataclass(frozen=True)
class DatasetExample:
    example_id: str
    dataset_id: str
    split: DatasetSplit
    format_type: FormatType
    input: str
    output: str
    metadata: dict
    content_hash: str


@dataclass(frozen=True)
class DatasetVersion:
    dataset_id: str
    version: int
    namespace: str
    counts: dict
    manifest_hash: str
    source_hashes: tuple[str, ...]
    schema_version: str = "1.0"


@dataclass(frozen=True)
class BusinessOutcome:
    outcome_id: str
    namespace: str
    candidate_id: str
    external_content_hash: str
    published_at: str
    metrics: dict
    revenue: float
    currency: str
    observation_window: str
    verified: bool
    source_type: str
    quality_pass: bool


@dataclass(frozen=True)
class EvalComparison:
    total_score: float
    dimension_scores: dict
    pass_rate: float
    regressions: tuple[str, ...]
    wins: int
    losses: int
    ties: int
    promotion_allowed: bool
    denial_reasons: tuple[str, ...]
    eval_run_id: str | None = None


@dataclass(frozen=True)
class TrainingJobSpec:
    namespace: str
    dataset_manifest_hash: str
    base_model: str
    config_hash: str


@dataclass(frozen=True)
class DatasetBuildJobSpec:
    namespace: str
    fixed_seed: int = 20260821


@dataclass(frozen=True)
class EvalJobSpec:
    namespace: str
    base_model: str
    adapter_id: str | None


@dataclass(frozen=True)
class AdapterPromotionJobSpec:
    adapter_id: str
    expected_eval_run_id: str


class ContentStore(Protocol):
    def put(self, content: str) -> str: ...
    def get(self, reference: str) -> str: ...
    def delete(self, reference: str) -> bool: ...


class BoundedLocalContentStore:
    def __init__(self, root: Path = LEARNING_RUNTIME / "content", max_bytes: int = 100_000_000,
                 retention_days: int = 30, max_item_bytes: int = MAX_FIELD_BYTES):
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.max_item_bytes = max_item_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _path(self, reference: str) -> Path:
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", reference):
            raise ValueError("invalid content reference")
        path = (self.root / reference.split(":", 1)[1][:2] / reference.split(":", 1)[1]).resolve()
        if not path.is_relative_to(self.root):
            raise PermissionError("content path traversal denied")
        return path

    def used_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file() and not path.is_symlink())

    def put(self, content: str) -> str:
        payload = content.encode("utf-8")
        if not payload or len(payload) > self.max_item_bytes:
            raise ValueError("content size outside bounded policy")
        digest = hashlib.sha256(payload).hexdigest()
        reference = f"sha256:{digest}"
        path = self._path(reference)
        if path.exists():
            if path.is_symlink() or path.read_bytes() != payload:
                raise RuntimeError("content-address collision or symlink")
            return reference
        if self.used_bytes() + len(payload) > self.max_bytes:
            raise OSError("content quota exceeded")
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return reference

    def get(self, reference: str) -> str:
        path = self._path(reference)
        if path.is_symlink() or not path.is_file():
            raise KeyError("content not found")
        return path.read_text(encoding="utf-8")

    def delete(self, reference: str) -> bool:
        path = self._path(reference)
        if path.is_symlink():
            raise PermissionError("refusing symlink content")
        if not path.exists():
            return False
        path.unlink()
        return True

    def cleanup(self, dry_run: bool = True, now: float | None = None) -> list[str]:
        cutoff = (now or time.time()) - self.retention_days * 86400
        expired = [path for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()
                   and path.stat().st_mtime < cutoff]
        references = [f"sha256:{path.name}" for path in expired]
        if not dry_run:
            raise PermissionError("V0.1 purge requires reference-aware maintenance and explicit approval")
        return references


class S3CompatibleContentStore:
    """Interface skeleton only. No endpoint or credential is configured in V0.1."""

    def __init__(self, endpoint: str | None = None):
        self.endpoint = endpoint

    def _disabled(self):
        raise RuntimeError("S3 content store is not configured")

    def put(self, content: str) -> str: return self._disabled()
    def get(self, reference: str) -> str: return self._disabled()
    def delete(self, reference: str) -> bool: return self._disabled()


class PrivacyFilter:
    patterns = (
        ("EMAIL", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
        ("PHONE", re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)|(?<!\d)\+?[1-9]\d{7,14}(?!\d)")),
        ("IDENTITY", re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")),
        ("BANK_ACCOUNT", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
        ("PASSPORT", re.compile(r"\b[A-Z][0-9]{7,8}\b", re.I)),
        ("FULL_ADDRESS", re.compile(r"(?:省|市|自治区|区|县).{2,40}(?:路|街|道|巷|号)")),
    )

    def redact(self, text: str) -> tuple[str, tuple[str, ...]]:
        labels = []
        result = text
        for label, pattern in self.patterns:
            def replace(match):
                digest = hashlib.sha256(match.group(0).encode()).hexdigest()[:12]
                labels.append(label)
                return f"[{label}:{digest}]"
            result = pattern.sub(replace, result)
        return result, tuple(sorted(set(labels)))


class LearningRepository:
    def __init__(self, path: Path = LEARNING_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._harden_permissions()

    def _harden_permissions(self) -> None:
        for path in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if path.exists() and not path.is_symlink():
                os.chmod(path, 0o600)

    def migrate(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS learning_candidates(
              candidate_id TEXT PRIMARY KEY,user_scope TEXT NOT NULL,project_scope TEXT NOT NULL,
              source_type TEXT NOT NULL,source_ref_hash TEXT NOT NULL,prompt_ref TEXT,response_ref TEXT,
              feedback TEXT NOT NULL,quality_labels_json TEXT NOT NULL,privacy_labels_json TEXT NOT NULL,
              synthetic_flag INTEGER NOT NULL,owner_approved INTEGER NOT NULL,
              deterministic_validated INTEGER NOT NULL DEFAULT 0,business_outcome_validated INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,status TEXT NOT NULL,namespace TEXT NOT NULL,
              deleted_at TEXT,rejection_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS preference_pairs(
              pair_id TEXT PRIMARY KEY,prompt_ref TEXT NOT NULL,chosen_ref TEXT NOT NULL,rejected_ref TEXT NOT NULL,
              reason TEXT NOT NULL,source_candidate_ids_json TEXT NOT NULL,owner_confirmed INTEGER NOT NULL,
              business_outcome_confirmed INTEGER NOT NULL,created_at TEXT NOT NULL,deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS datasets(
              dataset_id TEXT PRIMARY KEY,version INTEGER NOT NULL,namespace TEXT NOT NULL,created_at TEXT NOT NULL,
              immutable INTEGER NOT NULL DEFAULT 1,schema_version TEXT NOT NULL,manifest_hash TEXT NOT NULL UNIQUE
              ,UNIQUE(namespace,version)
            );
            CREATE TABLE IF NOT EXISTS dataset_examples(
              example_id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,split TEXT NOT NULL,format_type TEXT NOT NULL,
              input_ref TEXT NOT NULL,output_ref TEXT NOT NULL,metadata_json TEXT NOT NULL,content_hash TEXT NOT NULL,
              UNIQUE(dataset_id,content_hash),FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
            );
            CREATE TABLE IF NOT EXISTS dataset_manifests(
              dataset_id TEXT PRIMARY KEY,manifest_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,
              FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
            );
            CREATE TABLE IF NOT EXISTS adapter_versions(
              adapter_id TEXT PRIMARY KEY,name TEXT NOT NULL,namespace TEXT NOT NULL,base_model TEXT NOT NULL,
              base_model_revision TEXT NOT NULL,dataset_manifest_hash TEXT NOT NULL,training_config_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,status TEXT NOT NULL,eval_summary_json TEXT NOT NULL,
              artifact_path TEXT NOT NULL,artifact_hash TEXT NOT NULL,rollback_target TEXT
            );
            CREATE TABLE IF NOT EXISTS eval_runs(
              eval_run_id TEXT PRIMARY KEY,namespace TEXT NOT NULL,base_model TEXT NOT NULL,adapter_id TEXT,
              created_at TEXT NOT NULL,total_score REAL NOT NULL,pass_rate REAL NOT NULL,status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS eval_results(
              result_id TEXT PRIMARY KEY,eval_run_id TEXT NOT NULL,dimension TEXT NOT NULL,base_score REAL NOT NULL,
              adapter_score REAL NOT NULL,critical INTEGER NOT NULL,verdict TEXT NOT NULL,
              FOREIGN KEY(eval_run_id) REFERENCES eval_runs(eval_run_id)
            );
            CREATE TABLE IF NOT EXISTS business_outcomes(
              outcome_id TEXT PRIMARY KEY,namespace TEXT NOT NULL,candidate_id TEXT NOT NULL,
              external_content_hash TEXT NOT NULL,published_at TEXT NOT NULL,metrics_json TEXT NOT NULL,
              revenue REAL NOT NULL,currency TEXT NOT NULL,observation_window TEXT NOT NULL,
              verified INTEGER NOT NULL,source_type TEXT NOT NULL,quality_pass INTEGER NOT NULL,created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_events(
              event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,created_at TEXT NOT NULL,
              candidate_id TEXT,payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS learning_candidates_status_idx ON learning_candidates(namespace,status,created_at);
            CREATE INDEX IF NOT EXISTS learning_examples_split_idx ON dataset_examples(dataset_id,split);
            CREATE TRIGGER IF NOT EXISTS immutable_datasets_update BEFORE UPDATE ON datasets
              BEGIN SELECT RAISE(ABORT,'datasets are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_datasets_delete BEFORE DELETE ON datasets
              BEGIN SELECT RAISE(ABORT,'datasets are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_examples_update BEFORE UPDATE ON dataset_examples
              BEGIN SELECT RAISE(ABORT,'dataset examples are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_examples_delete BEFORE DELETE ON dataset_examples
              BEGIN SELECT RAISE(ABORT,'dataset examples are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_manifests_update BEFORE UPDATE ON dataset_manifests
              BEGIN SELECT RAISE(ABORT,'dataset manifests are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_manifests_delete BEFORE DELETE ON dataset_manifests
              BEGIN SELECT RAISE(ABORT,'dataset manifests are immutable'); END;
            """
        )
        self.db.commit()
        self._harden_permissions()

    def close(self) -> None: self.db.close()

    def event(self, event_type: str, candidate_id: str | None = None, payload: Mapping | None = None) -> None:
        safe = {}
        for key, value in dict(payload or {}).items():
            text = str(value)
            safe[key] = ({"sha256": hashlib.sha256(text.encode()).hexdigest(), "redacted": True}
                         if SecretFirewall().inspect(text).action == "BLOCK" else value)
        encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True)
        if len(encoded) > 2048:
            encoded = json.dumps({"truncated": True, "sha256": hashlib.sha256(encoded.encode()).hexdigest()})
        with self.db:
            self.db.execute("INSERT INTO learning_events VALUES(?,?,?,?,?)",
                            (str(uuid.uuid4()), event_type, utc_now(), candidate_id, encoded))

    def metrics(self) -> dict:
        scalar = lambda sql: self.db.execute(sql).fetchone()[0]
        latest = self.db.execute("SELECT total_score FROM eval_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        return {
            "candidate_count": scalar("SELECT COUNT(*) FROM learning_candidates WHERE deleted_at IS NULL"),
            "approved_count": scalar("SELECT COUNT(*) FROM learning_candidates WHERE status='APPROVED' AND deleted_at IS NULL"),
            "rejected_count": scalar("SELECT COUNT(*) FROM learning_candidates WHERE status='REJECTED' AND deleted_at IS NULL"),
            "dataset_count": scalar("SELECT COUNT(*) FROM datasets"),
            "golden_eval_count": scalar("SELECT COUNT(*) FROM dataset_examples WHERE split='GOLDEN_HOLDOUT'"),
            "adapter_count": scalar("SELECT COUNT(*) FROM adapter_versions"),
            "active_adapter_count": scalar("SELECT COUNT(*) FROM adapter_versions WHERE status='ACTIVE'"),
            "secret_rejection_count": scalar("SELECT COUNT(*) FROM learning_candidates WHERE rejection_reason='REJECTED_SECRET'"),
            "privacy_redaction_count": scalar("SELECT COUNT(*) FROM learning_candidates WHERE privacy_labels_json!='[]'"),
            "latest_eval_score": latest[0] if latest else None,
        }


class LearningService:
    def __init__(self, repository: LearningRepository, content_store: ContentStore,
                 firewall: SecretFirewall | None = None, privacy: PrivacyFilter | None = None):
        self.repository = repository
        self.content_store = content_store
        self.firewall = firewall or SecretFirewall()
        self.privacy = privacy or PrivacyFilter()

    def _validate(self, namespace: str, prompt: str, response: str, quality_labels: Sequence[str]) -> None:
        if namespace not in DATASET_NAMESPACES:
            raise ValueError("unknown dataset namespace")
        if not prompt.strip() or not response.strip():
            raise ValueError("prompt and response are required")
        if len(prompt.encode()) > MAX_FIELD_BYTES or len(response.encode()) > MAX_FIELD_BYTES:
            raise ValueError("candidate field exceeds size limit")
        if set(quality_labels) - QUALITY_LABELS:
            raise ValueError("unknown quality label")

    def capture_candidate(
        self, *, user_scope: str, namespace: str, project_scope: str, source_type: SourceType,
        source_ref: str, prompt: str, response: str, feedback: str = "MANUAL",
        quality_labels: Sequence[str] = (), synthetic_flag: bool = False,
        owner_approved: bool = False, deterministic_validated: bool = False,
        business_outcome_validated: bool = False, status: CandidateStatus | None = None,
    ) -> TrainingCandidate | None:
        self._validate(namespace, prompt, response, quality_labels)
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", project_scope):
            raise ValueError("project_scope must be a safe identifier")
        synthetic_flag = bool(synthetic_flag or source_type is SourceType.SYNTHETIC)
        if user_scope != "OWNER_PRIVATE":
            self.repository.event("PUBLIC_TRAINING_SKIPPED", payload={"scope_hash": content_hash(user_scope)})
            return None
        candidate_id = str(uuid.uuid4())
        source_ref_hash = content_hash(source_ref)
        existing = self.repository.db.execute(
            """SELECT candidate_id FROM learning_candidates WHERE source_ref_hash=? AND feedback=?
               AND source_type=? AND namespace=? AND deleted_at IS NULL LIMIT 1""",
            (source_ref_hash, feedback, source_type.value, namespace),
        ).fetchone()
        if existing:
            return self.get_candidate(existing["candidate_id"], include_content=False)
        combined = f"{prompt}\n{response}"
        decision = self.firewall.inspect(combined)
        if decision.action != "ALLOW":
            with self.repository.db:
                self.repository.db.execute(
                    """INSERT INTO learning_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (candidate_id, user_scope, project_scope, source_type.value, source_ref_hash, None, None,
                     feedback, "[]", "[]", int(synthetic_flag), int(owner_approved),
                     int(deterministic_validated), int(business_outcome_validated), utc_now(),
                     CandidateStatus.REJECTED.value, namespace, None, "REJECTED_SECRET"),
                )
            self.repository.event("CANDIDATE_REJECTED_SECRET", candidate_id, {"category": decision.category or "secret"})
            return self.get_candidate(candidate_id, include_content=False)

        clean_prompt, prompt_labels = self.privacy.redact(canonical_text(prompt))
        clean_response, response_labels = self.privacy.redact(canonical_text(response))
        privacy_labels = tuple(sorted(set(prompt_labels + response_labels)))
        resolved_status = status or (CandidateStatus.APPROVED if owner_approved else
                                     CandidateStatus.REDACTED if privacy_labels else CandidateStatus.PENDING)
        if synthetic_flag and not (owner_approved or deterministic_validated or business_outcome_validated):
            resolved_status = CandidateStatus.PENDING
        prompt_ref = self.content_store.put(clean_prompt)
        response_ref = self.content_store.put(clean_response)
        with self.repository.db:
            self.repository.db.execute(
                """INSERT INTO learning_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate_id, user_scope, project_scope, source_type.value, source_ref_hash,
                 prompt_ref, response_ref, feedback, json.dumps(sorted(set(quality_labels))),
                 json.dumps(privacy_labels), int(synthetic_flag), int(owner_approved),
                 int(deterministic_validated), int(business_outcome_validated), utc_now(),
                 resolved_status.value, namespace, None, None),
            )
        self.repository.event("CANDIDATE_CREATED", candidate_id, {
            "status": resolved_status.value, "namespace": namespace,
            "privacy_labels": privacy_labels, "synthetic": synthetic_flag,
        })
        return self.get_candidate(candidate_id)

    def get_candidate(self, candidate_id: str, include_content: bool = True) -> TrainingCandidate:
        row = self.repository.db.execute(
            "SELECT * FROM learning_candidates WHERE candidate_id=? AND deleted_at IS NULL", (candidate_id,)
        ).fetchone()
        if not row:
            raise KeyError("candidate not found")
        prompt = self.content_store.get(row["prompt_ref"]) if include_content and row["prompt_ref"] else None
        response = self.content_store.get(row["response_ref"]) if include_content and row["response_ref"] else None
        return TrainingCandidate(
            row["candidate_id"], row["user_scope"], row["project_scope"], SourceType(row["source_type"]),
            row["source_ref_hash"], prompt, response, row["feedback"],
            tuple(json.loads(row["quality_labels_json"])), tuple(json.loads(row["privacy_labels_json"])),
            bool(row["synthetic_flag"]), bool(row["owner_approved"]), row["created_at"],
            CandidateStatus(row["status"]), row["namespace"], bool(row["deterministic_validated"]),
            bool(row["business_outcome_validated"]),
        )

    def list_candidates(self, namespace: str | None = None, limit: int = 100) -> list[TrainingCandidate]:
        sql, values = "SELECT candidate_id FROM learning_candidates WHERE deleted_at IS NULL", []
        if namespace:
            sql += " AND namespace=?"; values.append(namespace)
        sql += " ORDER BY created_at DESC LIMIT ?"; values.append(min(max(limit, 1), 500))
        return [self.get_candidate(row["candidate_id"], include_content=False)
                for row in self.repository.db.execute(sql, values).fetchall()]

    def approve(self, candidate_id: str) -> TrainingCandidate:
        candidate = self.get_candidate(candidate_id)
        if candidate.status is CandidateStatus.REJECTED:
            raise ValueError("rejected candidate cannot be approved")
        with self.repository.db:
            self.repository.db.execute(
                "UPDATE learning_candidates SET status='APPROVED',owner_approved=1 WHERE candidate_id=?",
                (candidate_id,),
            )
        self.repository.event("CANDIDATE_APPROVED", candidate_id)
        return self.get_candidate(candidate_id)

    def delete_candidate(self, candidate_id: str) -> dict:
        row = self.repository.db.execute(
            "SELECT * FROM learning_candidates WHERE candidate_id=? AND deleted_at IS NULL", (candidate_id,)
        ).fetchone()
        if not row:
            return {"deleted": False, "data_removed_from_future_training": True,
                    "existing_adapter_may_contain_learned_effect": True}
        with self.repository.db:
            self.repository.db.execute(
                "UPDATE learning_candidates SET deleted_at=?,status='EXPIRED' WHERE candidate_id=?",
                (utc_now(), candidate_id),
            )
            self.repository.db.execute(
                "UPDATE preference_pairs SET deleted_at=? WHERE deleted_at IS NULL AND source_candidate_ids_json LIKE ?",
                (utc_now(), f'%"{candidate_id}"%'),
            )
        for reference in (row["prompt_ref"], row["response_ref"]):
            if reference:
                used = self.repository.db.execute(
                    """SELECT 1 FROM learning_candidates WHERE deleted_at IS NULL
                       AND (prompt_ref=? OR response_ref=?) LIMIT 1""", (reference, reference)
                ).fetchone()
                if not used:
                    used = self.repository.db.execute(
                        """SELECT 1 FROM dataset_examples WHERE input_ref=? OR output_ref=?
                           UNION SELECT 1 FROM preference_pairs WHERE deleted_at IS NULL
                           AND (prompt_ref=? OR chosen_ref=? OR rejected_ref=?) LIMIT 1""",
                        (reference, reference, reference, reference, reference),
                    ).fetchone()
                if not used:
                    self.content_store.delete(reference)
        self.repository.event("CANDIDATE_DELETED", candidate_id)
        return {"deleted": True, "data_removed_from_future_training": True,
                "existing_adapter_may_contain_learned_effect": True}


class FeedbackService:
    def __init__(self, learning: LearningService):
        self.learning = learning

    def record(self, *, feedback: FeedbackType, prompt: str, response: str, namespace: str,
               source_ref: str, better_response: str | None = None,
               quality_labels: Sequence[str] = ()) -> tuple[TrainingCandidate | None, PreferencePair | None]:
        if feedback is FeedbackType.SKIP:
            self.learning.repository.event("FEEDBACK_SKIPPED", payload={"source_ref_hash": content_hash(source_ref)})
            return None, None
        if feedback is FeedbackType.GOOD:
            candidate = self.learning.capture_candidate(
                user_scope="OWNER_PRIVATE", namespace=namespace, project_scope="owner",
                source_type=SourceType.TELEGRAM_OWNER_CHAT, source_ref=source_ref,
                prompt=prompt, response=response, feedback=feedback.value,
                quality_labels=quality_labels, owner_approved=True, synthetic_flag=True,
            )
            return candidate, None
        rejected = self.learning.capture_candidate(
            user_scope="OWNER_PRIVATE", namespace=namespace, project_scope="owner",
            source_type=SourceType.TELEGRAM_OWNER_CHAT, source_ref=source_ref,
            prompt=prompt, response=response, feedback=feedback.value,
            quality_labels=quality_labels, status=CandidateStatus.REJECTED, synthetic_flag=True,
        )
        if feedback is FeedbackType.BAD:
            return rejected, None
        if not better_response:
            raise ValueError("BETTER_RESPONSE requires chosen response")
        chosen = self.learning.capture_candidate(
            user_scope="OWNER_PRIVATE", namespace=namespace, project_scope="owner",
            source_type=SourceType.REVIEW_REVISION, source_ref=source_ref,
            prompt=prompt, response=better_response, feedback=feedback.value,
            quality_labels=quality_labels, owner_approved=True,
        )
        if not rejected or not chosen or not rejected.prompt or not rejected.response or not chosen.response:
            raise ValueError("preference content rejected by safety policy")
        pair = PreferencePair(str(uuid.uuid4()), chosen.prompt or prompt, chosen.response, rejected.response,
                              "owner correction", (rejected.candidate_id, chosen.candidate_id), True, False)
        store = self.learning.content_store
        with self.learning.repository.db:
            self.learning.repository.db.execute(
                "INSERT INTO preference_pairs VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                (pair.pair_id, store.put(pair.prompt), store.put(pair.chosen_response),
                 store.put(pair.rejected_response), pair.reason, json.dumps(pair.source_candidate_ids),
                 1, 0, utc_now()),
            )
        self.learning.repository.event("PREFERENCE_PAIR_CREATED", chosen.candidate_id,
                                       {"pair_id_hash": content_hash(pair.pair_id)})
        return chosen, pair

    def delete_pair(self, pair_id: str) -> bool:
        with self.learning.repository.db:
            cursor = self.learning.repository.db.execute(
                "UPDATE preference_pairs SET deleted_at=? WHERE pair_id=? AND deleted_at IS NULL",
                (utc_now(), pair_id),
            )
        if cursor.rowcount:
            self.learning.repository.event("PREFERENCE_PAIR_DELETED", payload={"pair_id_hash": content_hash(pair_id)})
        return bool(cursor.rowcount)


class DatasetBuilder:
    def __init__(self, repository: LearningRepository, content_store: ContentStore, fixed_seed: int = 20260821):
        self.repository = repository
        self.content_store = content_store
        self.fixed_seed = fixed_seed

    def _eligible(self, row) -> bool:
        if row["status"] not in {CandidateStatus.APPROVED.value, CandidateStatus.DATASET_ASSIGNED.value} or row["deleted_at"] is not None:
            return False
        if row["user_scope"] != "OWNER_PRIVATE":
            return False
        if row["synthetic_flag"] and not (
            row["owner_approved"] or row["deterministic_validated"] or row["business_outcome_validated"]
        ):
            return False
        return bool(row["prompt_ref"] and row["response_ref"])

    def build(self, namespace: str, golden_candidate_ids: Sequence[str] = ()) -> DatasetVersion:
        if namespace not in DATASET_NAMESPACES:
            raise ValueError("unknown dataset namespace")
        rows = self.repository.db.execute(
            "SELECT * FROM learning_candidates WHERE namespace=? AND deleted_at IS NULL ORDER BY candidate_id",
            (namespace,),
        ).fetchall()
        golden = set(golden_candidate_ids)
        records, seen = [], set()
        for row in rows:
            if not self._eligible(row):
                continue
            prompt, output = self.content_store.get(row["prompt_ref"]), self.content_store.get(row["response_ref"])
            digest = content_hash(prompt, output)
            if digest in seen:
                continue
            seen.add(digest)
            records.append((row, prompt, output, digest))
        pair_records = []
        pairs = self.repository.db.execute(
            "SELECT * FROM preference_pairs WHERE deleted_at IS NULL ORDER BY pair_id"
        ).fetchall()
        for pair in pairs:
            source_ids = json.loads(pair["source_candidate_ids_json"])
            source_rows = self.repository.db.execute(
                f"SELECT * FROM learning_candidates WHERE candidate_id IN ({','.join('?' for _ in source_ids)})",
                source_ids,
            ).fetchall() if source_ids else []
            if not source_rows or any(row["namespace"] != namespace or row["deleted_at"] for row in source_rows):
                continue
            if not (pair["owner_confirmed"] or pair["business_outcome_confirmed"]):
                continue
            prompt = self.content_store.get(pair["prompt_ref"])
            chosen = self.content_store.get(pair["chosen_ref"])
            rejected = self.content_store.get(pair["rejected_ref"])
            digest = content_hash(prompt, chosen, rejected)
            if digest in seen:
                continue
            seen.add(digest)
            pair_records.append((pair, prompt, chosen, rejected, digest))
        rng = random.Random(self.fixed_seed)
        regular = [record for record in records if record[0]["candidate_id"] not in golden]
        rng.shuffle(regular)
        split_map = {}
        for index, record in enumerate(regular):
            bucket = int(hashlib.sha256(f"{self.fixed_seed}:{record[3]}".encode()).hexdigest(), 16) % 100
            split_map[record[3]] = DatasetSplit.TRAIN if bucket < 80 else DatasetSplit.VALIDATION if bucket < 90 else DatasetSplit.TEST
        for record in records:
            if record[0]["candidate_id"] in golden:
                split_map[record[3]] = DatasetSplit.GOLDEN_HOLDOUT
        for pair, prompt, chosen, rejected, digest in pair_records:
            bucket = int(hashlib.sha256(f"{self.fixed_seed}:{digest}".encode()).hexdigest(), 16) % 100
            split_map[digest] = DatasetSplit.TRAIN if bucket < 80 else DatasetSplit.VALIDATION if bucket < 90 else DatasetSplit.TEST
        historical = self.repository.db.execute(
            """SELECT e.content_hash,e.split FROM dataset_examples e
               JOIN datasets d ON d.dataset_id=e.dataset_id WHERE d.namespace=?""", (namespace,)
        ).fetchall()
        historical_splits: dict[str, set[str]] = {}
        for item in historical:
            historical_splits.setdefault(item["content_hash"], set()).add(item["split"])
        for digest, split in split_map.items():
            previous = historical_splits.get(digest, set())
            if split is DatasetSplit.GOLDEN_HOLDOUT and (previous - {DatasetSplit.GOLDEN_HOLDOUT.value}):
                raise ValueError("non-holdout example cannot become Golden Holdout")
            if split is not DatasetSplit.GOLDEN_HOLDOUT and DatasetSplit.GOLDEN_HOLDOUT.value in previous:
                raise ValueError("Golden Holdout cannot enter training dataset")
        version = self.repository.db.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM datasets WHERE namespace=?", (namespace,)
        ).fetchone()[0]
        dataset_id = f"{namespace}-v{version}"
        source_hashes = tuple(sorted([row["source_ref_hash"] for row, *_ in records] +
                                     [content_hash(pair["pair_id"]) for pair, *_ in pair_records]))
        manifest_core = {
            "dataset_id": dataset_id, "version": version, "namespace": namespace,
            "schema_version": "1.0", "fixed_seed": self.fixed_seed,
            "counts": {split.value: sum(value is split for value in split_map.values()) for split in DatasetSplit},
            "examples": [{"content_hash": digest, "split": split_map[digest].value,
                          "source_hash": row["source_ref_hash"], "synthetic": bool(row["synthetic_flag"]),
                          "owner_approved": bool(row["owner_approved"]),
                          "deterministic_validated": bool(row["deterministic_validated"]),
                          "business_outcome_validated": bool(row["business_outcome_validated"])}
                         for row, _, _, digest in records],
            "preference_examples": [{"content_hash": digest, "split": split_map[digest].value,
                                      "pair_id_hash": content_hash(pair["pair_id"]),
                                      "owner_confirmed": bool(pair["owner_confirmed"]),
                                      "business_outcome_confirmed": bool(pair["business_outcome_confirmed"])}
                                     for pair, _, _, _, digest in pair_records],
        }
        manifest_json = json.dumps(manifest_core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
        counts = {split.value: 0 for split in DatasetSplit}
        with self.repository.db:
            self.repository.db.execute(
                "INSERT INTO datasets VALUES(?,?,?,?,?,?,?)",
                (dataset_id, version, namespace, utc_now(), 1, "1.0", manifest_hash),
            )
            self.repository.db.execute("INSERT INTO dataset_manifests VALUES(?,?,?)",
                                       (dataset_id, manifest_json, manifest_hash))
            for row, prompt, output, digest in records:
                split = split_map[digest]
                counts[split.value] += 1
                metadata = {
                    "source_ref_hash": row["source_ref_hash"], "candidate_id_hash": content_hash(row["candidate_id"]),
                    "synthetic": bool(row["synthetic_flag"]), "owner_confirmed": bool(row["owner_approved"]),
                    "privacy_labels": json.loads(row["privacy_labels_json"]), "dataset_version": version,
                }
                self.repository.db.execute(
                    "INSERT INTO dataset_examples VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), dataset_id, split.value, FormatType.SFT.value,
                     self.content_store.put(prompt), self.content_store.put(output),
                     json.dumps(metadata, sort_keys=True), digest),
                )
                self.repository.db.execute(
                    "UPDATE learning_candidates SET status='DATASET_ASSIGNED' WHERE candidate_id=?",
                    (row["candidate_id"],),
                )
            for pair, prompt, chosen, rejected, digest in pair_records:
                split = split_map[digest]
                counts[split.value] += 1
                output = json.dumps({"chosen": chosen, "rejected": rejected}, ensure_ascii=False, sort_keys=True)
                metadata = {"pair_id_hash": content_hash(pair["pair_id"]),
                            "owner_confirmed": bool(pair["owner_confirmed"]),
                            "business_outcome_confirmed": bool(pair["business_outcome_confirmed"]),
                            "dataset_version": version}
                self.repository.db.execute(
                    "INSERT INTO dataset_examples VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), dataset_id, split.value, FormatType.PREFERENCE.value,
                     self.content_store.put(prompt), self.content_store.put(output),
                     json.dumps(metadata, sort_keys=True), digest),
                )
        self.repository.event("DATASET_BUILT", payload={"dataset_id": dataset_id, "manifest_hash": manifest_hash,
                                                        "counts": counts})
        return DatasetVersion(dataset_id, version, namespace, counts, manifest_hash, source_hashes)

    def examples(self, dataset_id: str) -> list[DatasetExample]:
        rows = self.repository.db.execute(
            "SELECT * FROM dataset_examples WHERE dataset_id=? ORDER BY content_hash", (dataset_id,)
        ).fetchall()
        return [DatasetExample(row["example_id"], row["dataset_id"], DatasetSplit(row["split"]),
                               FormatType(row["format_type"]), self.content_store.get(row["input_ref"]),
                               self.content_store.get(row["output_ref"]), json.loads(row["metadata_json"]),
                               row["content_hash"]) for row in rows]


EVAL_DIMENSIONS = (
    "instruction_following", "format_correctness", "factual_consistency", "security_compliance",
    "code_quality", "project_behavior", "business_task_quality",
)
X_CONTENT_DIMENSIONS = (
    "hook_quality", "clarity", "factual_discipline", "no_invented_source",
    "platform_fit", "conciseness", "cta_quality", "safety",
)


class GoldenEvalHarness:
    """Deterministic/human scores only; no model judge is trusted by default."""

    critical_dimensions = {"security_compliance", "code_quality", "safety", "factual_discipline"}

    def __init__(self, repository: LearningRepository):
        self.repository = repository

    def compare(self, namespace: str, base_scores: Mapping[str, float], adapter_scores: Mapping[str, float],
                adapter_id: str | None = None, overall_threshold: float = 0.02,
                business_threshold: float = 0.0, catastrophic_drop: float = 0.20) -> EvalComparison:
        dimensions = X_CONTENT_DIMENSIONS if namespace == "x-content" else EVAL_DIMENSIONS
        if set(base_scores) != set(dimensions) or set(adapter_scores) != set(dimensions):
            raise ValueError("eval dimensions do not match namespace policy")
        if not base_scores or any(not 0 <= value <= 1 for value in (*base_scores.values(), *adapter_scores.values())):
            raise ValueError("eval scores must be within 0..1")
        changes = {key: adapter_scores[key] - base_scores[key] for key in base_scores}
        regressions = tuple(sorted(key for key, change in changes.items() if change < 0))
        denial = []
        for key in self.critical_dimensions & changes.keys():
            if changes[key] < 0:
                denial.append(f"critical regression:{key}")
        if min(changes.values()) < -catastrophic_drop:
            denial.append("catastrophic regression")
        base_total = sum(base_scores.values()) / len(base_scores)
        adapter_total = sum(adapter_scores.values()) / len(adapter_scores)
        if adapter_total - base_total < overall_threshold:
            denial.append("overall improvement below threshold")
        if namespace in {"x-content", "stickers-content", "livestream-content", "novel-editor"}:
            business_keys = set(changes) & {"business_task_quality", "hook_quality", "platform_fit", "cta_quality"}
            if business_keys and sum(changes[key] for key in business_keys) / len(business_keys) < business_threshold:
                denial.append("business improvement below threshold")
        wins = sum(value > 0 for value in changes.values())
        losses = sum(value < 0 for value in changes.values())
        ties = sum(value == 0 for value in changes.values())
        eval_run_id = str(uuid.uuid4())
        comparison = EvalComparison(
            round(adapter_total, 6), {key: round(value, 6) for key, value in adapter_scores.items()},
            round(sum(value >= 0.7 for value in adapter_scores.values()) / len(adapter_scores), 6),
            regressions, wins, losses, ties, not denial, tuple(denial), eval_run_id,
        )
        with self.repository.db:
            self.repository.db.execute(
                "INSERT INTO eval_runs VALUES(?,?,?,?,?,?,?,?)",
                (eval_run_id, namespace, BASE_MODEL, adapter_id, utc_now(), comparison.total_score,
                 comparison.pass_rate, "PASS" if comparison.promotion_allowed else "FAIL"),
            )
            for dimension in base_scores:
                verdict = "WIN" if changes[dimension] > 0 else "LOSS" if changes[dimension] < 0 else "TIE"
                self.repository.db.execute(
                    "INSERT INTO eval_results VALUES(?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), eval_run_id, dimension, base_scores[dimension], adapter_scores[dimension],
                     int(dimension in self.critical_dimensions), verdict),
                )
        self.repository.event("EVAL_COMPLETED", payload={"eval_run_id": eval_run_id,
                                                         "promotion_allowed": comparison.promotion_allowed})
        return comparison


class AdapterRegistry:
    def __init__(self, repository: LearningRepository, artifact_root: Path = LEARNING_RUNTIME / "adapters"):
        self.repository = repository
        self.artifact_root = Path(artifact_root).resolve()

    def register(self, *, name: str, namespace: str, base_model_revision: str,
                 dataset_manifest_hash: str, training_config_hash: str,
                 artifact_path: Path, artifact_hash: str) -> str:
        if namespace not in DATASET_NAMESPACES:
            raise ValueError("unknown adapter namespace")
        path = Path(artifact_path).resolve()
        if not path.is_relative_to(self.artifact_root):
            raise PermissionError("adapter artifact path outside runtime")
        if not re.fullmatch(r"[a-f0-9]{64}", artifact_hash):
            raise ValueError("invalid adapter artifact hash")
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact_hash:
            raise ValueError("adapter artifact is missing or hash mismatched")
        adapter_id = str(uuid.uuid4())
        with self.repository.db:
            self.repository.db.execute(
                "INSERT INTO adapter_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (adapter_id, name, namespace, BASE_MODEL, base_model_revision, dataset_manifest_hash,
                 training_config_hash, utc_now(), AdapterStatus.CANDIDATE.value, "{}", str(path), artifact_hash, None),
            )
        return adapter_id

    def promote(self, adapter_id: str, comparison: EvalComparison) -> bool:
        row = self.repository.db.execute("SELECT * FROM adapter_versions WHERE adapter_id=?", (adapter_id,)).fetchone()
        if not row:
            raise KeyError("adapter not found")
        if row["status"] not in {AdapterStatus.CANDIDATE.value, AdapterStatus.EVAL_FAILED.value}:
            raise ValueError("adapter is not eligible for promotion")
        bound_eval = self.repository.db.execute(
            """SELECT eval_run_id FROM eval_runs WHERE eval_run_id=? AND adapter_id=? AND status='PASS'
               AND total_score=? LIMIT 1""", (comparison.eval_run_id, adapter_id, comparison.total_score)
        ).fetchone()
        if comparison.promotion_allowed and not bound_eval:
            raise PermissionError("adapter promotion requires a bound passing eval run")
        summary = json.dumps(asdict(comparison), ensure_ascii=False, sort_keys=True)
        if not comparison.promotion_allowed:
            with self.repository.db:
                self.repository.db.execute(
                    "UPDATE adapter_versions SET status=?,eval_summary_json=? WHERE adapter_id=?",
                    (AdapterStatus.EVAL_FAILED.value, summary, adapter_id),
                )
            return False
        current = self.repository.db.execute(
            "SELECT adapter_id FROM adapter_versions WHERE namespace=? AND status='ACTIVE'", (row["namespace"],)
        ).fetchone()
        with self.repository.db:
            if current:
                self.repository.db.execute(
                    "UPDATE adapter_versions SET status='ROLLED_BACK' WHERE adapter_id=?", (current["adapter_id"],)
                )
            self.repository.db.execute(
                "UPDATE adapter_versions SET status='ACTIVE',eval_summary_json=?,rollback_target=? WHERE adapter_id=?",
                (summary, current["adapter_id"] if current else None, adapter_id),
            )
        self.repository.event("ADAPTER_PROMOTED", payload={"adapter_id_hash": content_hash(adapter_id),
                                                            "namespace": row["namespace"]})
        return True

    def rollback(self, adapter_id: str) -> str | None:
        row = self.repository.db.execute("SELECT * FROM adapter_versions WHERE adapter_id=?", (adapter_id,)).fetchone()
        if not row or row["status"] != AdapterStatus.ACTIVE.value:
            raise ValueError("adapter is not active")
        target = row["rollback_target"]
        with self.repository.db:
            self.repository.db.execute("UPDATE adapter_versions SET status='ROLLED_BACK' WHERE adapter_id=?", (adapter_id,))
            if target:
                self.repository.db.execute("UPDATE adapter_versions SET status='ACTIVE' WHERE adapter_id=?", (target,))
        return target

    def list(self, namespace: str | None = None) -> list[dict]:
        sql, values = "SELECT * FROM adapter_versions", []
        if namespace:
            sql += " WHERE namespace=?"; values.append(namespace)
        sql += " ORDER BY created_at DESC"
        return [dict(row) for row in self.repository.db.execute(sql, values).fetchall()]


class BusinessOutcomeScorer:
    metric_weights = {
        "x-content": {"impressions": 0.05, "likes": 0.10, "replies": 0.15, "reposts": 0.15,
                      "clicks": 0.20, "conversions": 0.25, "revenue": 0.10},
        "stickers-content": {"views": 0.10, "downloads": 0.35, "purchases": 0.40, "revenue": 0.15},
        "livestream-content": {"views": 0.05, "watch_time": 0.20, "retention": 0.25,
                               "clicks": 0.10, "conversions": 0.30, "revenue": 0.10},
        "novel-editor": {"accepted": 0.35, "revision_count": -0.15, "reader_engagement": 0.40,
                         "retention_proxy": 0.25},
        "personal-general": {"owner_acceptance": 0.70, "revision_count": -0.30},
    }

    def score(self, outcome: BusinessOutcome) -> float:
        if not outcome.verified or not outcome.quality_pass:
            return 0.0
        weights = self.metric_weights.get(outcome.namespace, {})
        raw = 0.0
        for key, weight in weights.items():
            value = outcome.revenue if key == "revenue" else float(outcome.metrics.get(key, 0))
            normalized = min(max(value, 0), 100) / 100
            raw += normalized * weight
        return round(max(0.0, min(raw, 1.0)), 6)

    def record(self, repository: LearningRepository, outcome: BusinessOutcome) -> float:
        if outcome.namespace not in self.metric_weights:
            raise ValueError("unsupported business outcome namespace")
        allowed = set(self.metric_weights[outcome.namespace]) - {"revenue"}
        if set(outcome.metrics) - allowed:
            raise ValueError("unsupported business outcome metric")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
               or value < 0 or value > 1_000_000_000 for value in outcome.metrics.values()):
            raise ValueError("invalid business outcome metric value")
        if outcome.source_type not in {"MANUAL", "FIXTURE"}:
            raise ValueError("V0.1 business outcome source must be manual or fixture")
        if not re.fullmatch(r"[a-f0-9]{64}", outcome.external_content_hash):
            raise ValueError("external content must be represented by SHA-256")
        if not isinstance(outcome.revenue, (int, float)) or not math.isfinite(outcome.revenue) or outcome.revenue < 0:
            raise ValueError("invalid revenue")
        if not re.fullmatch(r"[A-Z]{3}", outcome.currency):
            raise ValueError("currency must be ISO-like code")
        score = self.score(outcome)
        with repository.db:
            repository.db.execute(
                "INSERT INTO business_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (outcome.outcome_id, outcome.namespace, outcome.candidate_id, outcome.external_content_hash,
                 outcome.published_at, json.dumps(outcome.metrics, sort_keys=True), outcome.revenue,
                 outcome.currency, outcome.observation_window, int(outcome.verified), outcome.source_type,
                 int(outcome.quality_pass), utc_now()),
            )
            if score > 0:
                repository.db.execute(
                    "UPDATE learning_candidates SET business_outcome_validated=1 WHERE candidate_id=?",
                    (outcome.candidate_id,),
                )
        repository.event("BUSINESS_OUTCOME_RECORDED", outcome.candidate_id,
                         {"score": score, "quality_pass": outcome.quality_pass, "verified": outcome.verified})
        return score


class TrainingPriorityService:
    def __init__(self, repository: LearningRepository, scorer: BusinessOutcomeScorer | None = None):
        self.repository = repository
        self.scorer = scorer or BusinessOutcomeScorer()

    def top(self, limit: int = 20) -> list[dict]:
        candidates = self.repository.db.execute(
            "SELECT * FROM learning_candidates WHERE deleted_at IS NULL ORDER BY created_at"
        ).fetchall()
        output = []
        for row in candidates:
            score, reasons = 0.0, []
            if row["feedback"] == FeedbackType.BETTER_RESPONSE.value:
                score += 5; reasons.append("owner corrected")
            elif row["feedback"] == FeedbackType.BAD.value:
                score += 3; reasons.append("owner rejected")
            if row["owner_approved"]:
                score += 2; reasons.append("owner approved")
            labels = set(json.loads(row["quality_labels_json"]))
            if "BUSINESS_EFFECTIVENESS" in labels:
                score += 2; reasons.append("business task")
            outcomes = self.repository.db.execute(
                "SELECT * FROM business_outcomes WHERE candidate_id=?", (row["candidate_id"],)
            ).fetchall()
            for outcome_row in outcomes:
                outcome = BusinessOutcome(
                    outcome_row["outcome_id"], outcome_row["namespace"], outcome_row["candidate_id"],
                    outcome_row["external_content_hash"], outcome_row["published_at"],
                    json.loads(outcome_row["metrics_json"]), outcome_row["revenue"], outcome_row["currency"],
                    outcome_row["observation_window"], bool(outcome_row["verified"]), outcome_row["source_type"],
                    bool(outcome_row["quality_pass"]),
                )
                business_score = self.scorer.score(outcome)
                if business_score:
                    score += business_score * 10; reasons.append("validated business outcome")
            if row["synthetic_flag"] and not row["owner_approved"]:
                score -= 5; reasons.append("synthetic only")
            output.append({"candidate_id": row["candidate_id"], "namespace": row["namespace"],
                           "priority_score": round(score, 4), "reasons": reasons})
        return sorted(output, key=lambda item: (-item["priority_score"], item["candidate_id"]))[:limit]


@dataclass(frozen=True)
class TrainingCapability:
    apple_silicon: bool
    physical_memory_gib: float | None
    disk_free_gib: float
    mlx_available: bool
    mlx_lm_available: bool
    training_venv_status: str
    qwen36_local_model_found: bool
    model_metadata_found: bool
    estimated_training_ready: bool
    probe_source: str


@dataclass(frozen=True)
class LoRAPilotConfig:
    base_model: str
    base_model_path: str
    adapter_path: str
    dataset_path: str
    batch_size: int
    learning_rate: float
    lora_rank: int
    layers: tuple[int, ...]
    modules: tuple[str, ...]
    iters: int
    validation_interval: int
    seed: int
    production_training_enabled: bool = False
    installed_cli_validated: bool = False

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


class TrainingProvider(Protocol):
    def probe(self) -> TrainingCapability: ...
    def prepare_dataset(self, dataset: DatasetVersion) -> dict: ...
    def build_config(self, **kwargs) -> LoRAPilotConfig: ...
    def train(self, config: LoRAPilotConfig) -> dict: ...
    def validate_artifact(self, path: Path, expected_hash: str) -> bool: ...


class MLXLoRATrainingProvider:
    """A safe disabled-by-default provider; it never downloads or mutates the base model."""

    def __init__(self, training_venv: Path = TRAINING_VENV, model_path: Path = LOCAL_MODEL,
                 adapter_root: Path = LEARNING_RUNTIME / "adapters"):
        self.training_venv = Path(training_venv)
        self.model_path = Path(model_path).resolve()
        self.adapter_root = Path(adapter_root).resolve()

    @staticmethod
    def _module_probe(python: Path, modules: Sequence[str]) -> dict[str, bool]:
        if not python.is_file():
            return {module: False for module in modules}
        code = "import importlib.util,json;print(json.dumps({m:importlib.util.find_spec(m) is not None for m in " + repr(list(modules)) + "}))"
        try:
            result = subprocess.run((str(python), "-c", code), capture_output=True, text=True,
                                    shell=False, timeout=10, check=False,
                                    env={"PATH": os.defpath, "PYTHONHASHSEED": "0"})
            return json.loads(result.stdout) if result.returncode == 0 else {module: False for module in modules}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return {module: False for module in modules}

    def probe(self) -> TrainingCapability:
        apple = platform.system() == "Darwin" and platform.machine() == "arm64"
        try:
            memory = int(subprocess.run(("sysctl", "-n", "hw.memsize"), capture_output=True, text=True,
                                        timeout=3, shell=False, check=False).stdout.strip()) / 1024 ** 3
        except (ValueError, OSError, subprocess.SubprocessError):
            memory = None
        disk = shutil.disk_usage(AI_ROOT).free / 1024 ** 3
        training_python = self.training_venv / "bin/python"
        training_status = "AVAILABLE" if training_python.is_file() else "NOT_CONFIGURED"
        modules = self._module_probe(training_python, ("mlx", "mlx_lm"))
        source = "training-venv"
        if training_status == "NOT_CONFIGURED":
            modules = self._module_probe(OMLX_VENV_PYTHON, ("mlx", "mlx_lm"))
            source = "omlx-venv-read-only-probe"
        metadata = (self.model_path / "config.json").is_file()
        model_found = (self.model_path.is_dir() and metadata
                       and any(self.model_path.glob("*.safetensors"))
                       and any((self.model_path / name).is_file() for name in ("tokenizer.json", "tokenizer_config.json")))
        ready = apple and memory is not None and memory >= 32 and disk >= 20 and all(modules.values()) and model_found and training_status == "AVAILABLE"
        return TrainingCapability(apple, round(memory, 2) if memory else None, round(disk, 2),
                                  modules.get("mlx", False), modules.get("mlx_lm", False), training_status,
                                  model_found, metadata, ready, source)

    def prepare_dataset(self, dataset: DatasetVersion) -> dict:
        return {"dataset_id": dataset.dataset_id, "manifest_hash": dataset.manifest_hash,
                "status": "SCHEMA_READY", "raw_content_exported": False}

    def build_config(self, *, dataset_path: Path, adapter_path: Path, batch_size: int = 1,
                     learning_rate: float = 1e-5, lora_rank: int = 8,
                     layers: Sequence[int] = (0, 1, 2, 3),
                     modules: Sequence[str] = ("q_proj", "v_proj"), iters: int = 3,
                     validation_interval: int = 1, seed: int = 20260821) -> LoRAPilotConfig:
        dataset = Path(dataset_path).resolve(); adapter = Path(adapter_path).resolve()
        if not dataset.is_relative_to(LEARNING_RUNTIME.resolve()):
            raise PermissionError("dataset path outside learning runtime")
        if not adapter.is_relative_to(self.adapter_root):
            raise PermissionError("adapter path outside learning runtime")
        if batch_size != 1 or not 1 <= lora_rank <= 16 or not 1 <= iters <= 3:
            raise ValueError("pilot configuration exceeds conservative bounds")
        if not 0 < learning_rate <= 1e-4 or not layers or not modules:
            raise ValueError("invalid pilot configuration")
        return LoRAPilotConfig(BASE_MODEL, str(self.model_path), str(adapter), str(dataset), batch_size,
                               learning_rate, lora_rank, tuple(int(value) for value in layers),
                               tuple(str(value) for value in modules), iters, validation_interval, seed,
                               production_training_enabled=False, installed_cli_validated=False)

    def train(self, config: LoRAPilotConfig) -> dict:
        return {"status": "DISABLED", "reason": "PRODUCTION_TRAINING_DISABLED_BY_DEFAULT",
                "config_hash": config.config_hash, "base_model_modified": False}

    def validate_artifact(self, path: Path, expected_hash: str) -> bool:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.adapter_root) or not resolved.is_file() or resolved.is_symlink():
            return False
        return hashlib.sha256(resolved.read_bytes()).hexdigest() == expected_hash


class LearningImportExport:
    def __init__(self, service: LearningService, root: Path = LEARNING_RUNTIME):
        self.service = service
        self.root = Path(root).resolve()
        self.import_root = self.root / "imports"
        self.export_root = self.root / "exports"
        self.import_root.mkdir(parents=True, exist_ok=True)
        self.export_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.import_root, 0o700)
        os.chmod(self.export_root, 0o700)

    def _safe_path(self, path: Path, expected_root: Path) -> Path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(expected_root.resolve()):
            raise PermissionError("learning path traversal denied")
        if Path(path).is_symlink():
            raise PermissionError("learning symlink denied")
        return resolved

    def import_jsonl(self, path: Path, namespace: str) -> dict:
        resolved = self._safe_path(path, self.import_root)
        if resolved.suffix.lower() != ".jsonl" or not resolved.is_file() or resolved.stat().st_size > MAX_IMPORT_BYTES:
            raise ValueError("import file missing or too large")
        created = rejected = duplicates = 0
        seen = set()
        for index, line in enumerate(resolved.read_text(encoding="utf-8").splitlines()):
            if index >= MAX_IMPORT_LINES:
                raise ValueError("import line limit exceeded")
            item = json.loads(line)
            if set(item) - {"prompt", "response", "source_ref", "quality_labels", "synthetic", "owner_approved"}:
                raise ValueError("unknown import field")
            prompt, response = item.get("prompt"), item.get("response")
            if not isinstance(prompt, str) or not isinstance(response, str):
                raise ValueError("invalid import schema")
            digest = content_hash(prompt, response)
            if digest in seen:
                duplicates += 1; continue
            seen.add(digest)
            candidate = self.service.capture_candidate(
                user_scope="OWNER_PRIVATE", namespace=namespace, project_scope="manual-import",
                source_type=SourceType.MANUAL_IMPORT, source_ref=str(item.get("source_ref", digest)),
                prompt=prompt, response=response, quality_labels=item.get("quality_labels", ()),
                synthetic_flag=bool(item.get("synthetic", False)),
                owner_approved=bool(item.get("owner_approved", False)),
            )
            if candidate and candidate.status is CandidateStatus.REJECTED: rejected += 1
            elif candidate: created += 1
        return {"created": created, "rejected": rejected, "duplicates": duplicates}

    def export_manifest(self, dataset_id: str, path: Path) -> Path:
        resolved = self._safe_path(path, self.export_root)
        row = self.service.repository.db.execute(
            "SELECT manifest_json FROM dataset_manifests WHERE dataset_id=?", (dataset_id,)
        ).fetchone()
        if not row:
            raise KeyError("dataset manifest not found")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(row["manifest_json"] + "\n", encoding="utf-8")
        os.chmod(resolved, 0o600)
        return resolved

    def export_redacted_jsonl(self, dataset_id: str, path: Path) -> Path:
        resolved = self._safe_path(path, self.export_root)
        examples = DatasetBuilder(self.service.repository, self.service.content_store).examples(dataset_id)
        lines = [json.dumps({"input": item.input, "output": item.output, "split": item.split.value,
                             "format_type": item.format_type.value, "content_hash": item.content_hash},
                            ensure_ascii=False, sort_keys=True) for item in examples]
        payload = ("\n".join(lines) + ("\n" if lines else ""))
        if len(payload.encode()) > MAX_IMPORT_BYTES:
            raise ValueError("export exceeds bounded size")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(payload, encoding="utf-8"); os.chmod(resolved, 0o600)
        return resolved


class TrainingFormatSerializer:
    def __init__(self, firewall: SecretFirewall | None = None):
        self.firewall = firewall or SecretFirewall()

    def _safe(self, *values: str) -> None:
        if any(not value or len(value.encode()) > MAX_FIELD_BYTES for value in values):
            raise ValueError("training format field outside bounded schema")
        if any(self.firewall.inspect(value).action != "ALLOW" for value in values):
            raise ValueError("training format rejected by Secret Firewall")

    def _metadata(self, metadata: Mapping | None) -> dict:
        value = dict(metadata or {})
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as error:
            raise ValueError("training metadata must be JSON serializable") from error
        if len(encoded.encode()) > MAX_FIELD_BYTES:
            raise ValueError("training metadata exceeds bounded schema")
        if self.firewall.inspect(encoded).action != "ALLOW":
            raise ValueError("training metadata rejected by Secret Firewall")
        return value

    def normalized_chat(self, prompt: str, response: str) -> dict:
        prompt, response = canonical_text(prompt), canonical_text(response)
        self._safe(prompt, response)
        return {"messages": [{"role": "user", "content": prompt},
                             {"role": "assistant", "content": response}]}

    def sft_jsonl(self, prompt: str, response: str, metadata: Mapping | None = None) -> str:
        value = self.normalized_chat(prompt, response) | {"metadata": self._metadata(metadata)}
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def preference_jsonl(self, prompt: str, chosen: str, rejected: str,
                         metadata: Mapping | None = None) -> str:
        prompt, chosen, rejected = map(canonical_text, (prompt, chosen, rejected))
        self._safe(prompt, chosen, rejected)
        return json.dumps({"prompt": prompt, "chosen": chosen, "rejected": rejected,
                           "metadata": self._metadata(metadata)}, ensure_ascii=False, sort_keys=True)


def retention_dry_run(repository: LearningRepository, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    pending_cutoff = (now - timedelta(days=30)).isoformat()
    rejected_cutoff = (now - timedelta(days=7)).isoformat()
    rows = repository.db.execute(
        """SELECT candidate_id,status FROM learning_candidates WHERE deleted_at IS NULL AND
           ((status='PENDING' AND created_at<?) OR (status='REJECTED' AND created_at<?))""",
        (pending_cutoff, rejected_cutoff),
    ).fetchall()
    return {"dry_run": True, "candidate_count": len(rows),
            "candidate_id_hashes": [content_hash(row["candidate_id"]) for row in rows], "deleted": 0}
