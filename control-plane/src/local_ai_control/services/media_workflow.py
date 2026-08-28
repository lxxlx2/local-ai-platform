"""Durable, private, restart-safe Media Product Workflow V0.2 contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .web_research import SafeHttpFetcher


SCHEMA_VERSION = "0.2"
DEFAULT_ROOT = Path("/Users/jerson/AI/runtime/media-jobs")
JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{3,79}$")
SLUG_RE = re.compile(r"[^a-z0-9]+")
SUPPORTED_UPLOADS = {".pptx", ".txt", ".md", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".mp4"}


class MediaWorkflowError(RuntimeError):
    pass


class MediaWorkflowState(StrEnum):
    RECEIVED = "RECEIVED"
    INPUT_PENDING = "INPUT_PENDING"
    REQUIREMENTS_PENDING = "REQUIREMENTS_PENDING"
    REQUIREMENTS_READY = "REQUIREMENTS_READY"
    MISSING_OWNER_FACT = "MISSING_OWNER_FACT"
    SCRIPT_PENDING = "SCRIPT_PENDING"
    SCRIPT_READY = "SCRIPT_READY"
    PROFILE_SELECTED = "PROFILE_SELECTED"
    ASSETS_READY = "ASSETS_READY"
    AUDIO_READY = "AUDIO_READY"
    VISUAL_READY = "VISUAL_READY"
    VIDEO_READY = "VIDEO_READY"
    REVIEW_PENDING = "REVIEW_PENDING"
    APPROVED = "APPROVED"
    PUBLISH_PENDING = "PUBLISH_PENDING"
    PUBLISHED = "PUBLISHED"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IntakeMode(StrEnum):
    UPLOADS = "UPLOADS"
    LINKS = "LINKS"
    UPLOADS_AND_LINKS = "UPLOADS_AND_LINKS"
    DIRECT_BRIEF = "DIRECT_BRIEF"


class CompletionMode(StrEnum):
    AUTO_COMPLETE = "AUTO_COMPLETE"
    SCRIPT_REVIEW_FIRST = "SCRIPT_REVIEW_FIRST"


TRANSITIONS = {
    MediaWorkflowState.RECEIVED: {MediaWorkflowState.INPUT_PENDING, MediaWorkflowState.REQUIREMENTS_PENDING, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.INPUT_PENDING: {MediaWorkflowState.REQUIREMENTS_PENDING, MediaWorkflowState.CANCELLED, MediaWorkflowState.FAILED},
    MediaWorkflowState.REQUIREMENTS_PENDING: {MediaWorkflowState.REQUIREMENTS_READY, MediaWorkflowState.MISSING_OWNER_FACT, MediaWorkflowState.FAILED, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.MISSING_OWNER_FACT: {MediaWorkflowState.REQUIREMENTS_PENDING, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.REQUIREMENTS_READY: {MediaWorkflowState.SCRIPT_PENDING, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.SCRIPT_PENDING: {MediaWorkflowState.SCRIPT_READY, MediaWorkflowState.MISSING_OWNER_FACT, MediaWorkflowState.FAILED, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.SCRIPT_READY: {MediaWorkflowState.PROFILE_SELECTED, MediaWorkflowState.SCRIPT_PENDING, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.PROFILE_SELECTED: {MediaWorkflowState.ASSETS_READY, MediaWorkflowState.AUDIO_READY, MediaWorkflowState.FAILED, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.ASSETS_READY: {MediaWorkflowState.AUDIO_READY, MediaWorkflowState.VISUAL_READY, MediaWorkflowState.FAILED, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.AUDIO_READY: {MediaWorkflowState.VISUAL_READY, MediaWorkflowState.VIDEO_READY, MediaWorkflowState.FAILED, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.VISUAL_READY: {MediaWorkflowState.VIDEO_READY, MediaWorkflowState.FAILED, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.VIDEO_READY: {MediaWorkflowState.REVIEW_PENDING, MediaWorkflowState.FAILED, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.REVIEW_PENDING: {MediaWorkflowState.APPROVED, MediaWorkflowState.SCRIPT_PENDING, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.APPROVED: {MediaWorkflowState.PUBLISH_PENDING, MediaWorkflowState.SCRIPT_PENDING, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.PUBLISH_PENDING: {MediaWorkflowState.PUBLISHED, MediaWorkflowState.FAILED, MediaWorkflowState.CANCELLED},
    MediaWorkflowState.PUBLISHED: {MediaWorkflowState.CLEANUP_PENDING, MediaWorkflowState.ARCHIVED},
    MediaWorkflowState.CLEANUP_PENDING: {MediaWorkflowState.ARCHIVED, MediaWorkflowState.FAILED},
    MediaWorkflowState.FAILED: {MediaWorkflowState.REQUIREMENTS_PENDING, MediaWorkflowState.SCRIPT_PENDING, MediaWorkflowState.PUBLISH_PENDING, MediaWorkflowState.CANCELLED},
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.lower()).strip("-")[:48]
    return slug or "media-product"


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


@dataclass(frozen=True)
class Provenance:
    source: str
    retrieved_at: str
    content_sha256: str
    category: str
    evidence_relation: str
    trust_label: str = "UNTRUSTED_EXTERNAL_CONTENT"


@dataclass(frozen=True)
class Requirements:
    objective: str
    deliverable_count: int = 1
    duration_constraints: str = ""
    format_constraints: tuple[str, ...] = ("MP4",)
    required_questions: tuple[str, ...] = ()
    language_requirements: str = "auto"
    submission_requirements: tuple[str, ...] = ()
    deadline: str | None = None
    evaluation_criteria: tuple[str, ...] = ()
    official_references: tuple[str, ...] = ()
    explicit_constraints: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()

    def validate(self) -> None:
        if not self.objective.strip() or not 1 <= self.deliverable_count <= 20:
            raise MediaWorkflowError("REQUIREMENTS_INVALID")
        if self.language_requirements not in {"auto", "zh", "en"}:
            raise MediaWorkflowError("REQUIREMENTS_LANGUAGE_INVALID")


@dataclass
class MediaProductJob:
    job_id: str
    owner_id: str
    task_name: str
    task_slug: str
    state: MediaWorkflowState
    intake_mode: IntakeMode
    completion_mode: CompletionMode
    candidate_revision: int
    created_at: str
    updated_at: str
    content_hashes: dict[str, str] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    failure: dict | None = None
    missing_owner_fact: str | None = None
    approval: dict | None = None
    publish: dict | None = None
    history: list[dict] = field(default_factory=list)


class MediaWorkspace:
    """One bounded private workspace; only relative artifact paths are persisted."""
    def __init__(self, job_id: str, root: Path | str = DEFAULT_ROOT):
        if not JOB_ID_RE.fullmatch(job_id):
            raise MediaWorkflowError("MEDIA_JOB_ID_INVALID")
        self.root = Path(root).resolve(); self.path = self.root / job_id

    def create(self, *, owner_id: str, task_name: str, intake_mode: IntakeMode, completion_mode: CompletionMode) -> MediaProductJob:
        if self.path.exists(): raise MediaWorkflowError("MEDIA_JOB_EXISTS")
        self.path.mkdir(mode=0o700, parents=True)
        for name in ("source", "evidence", "generated", "audio", "visual", "segments", "output", "metadata"):
            (self.path / name).mkdir(mode=0o700)
        now = utc_now()
        job = MediaProductJob(self.path.name, owner_id, task_name.strip(), slugify(task_name), MediaWorkflowState.RECEIVED,
                              IntakeMode(intake_mode), CompletionMode(completion_mode), 1, now, now)
        job.history.append({"at": now, "from": None, "to": job.state.value, "reason": "created"})
        self.save(job); return job

    @property
    def state_path(self) -> Path: return self.path / "job.json"

    def _inside(self, relative: str) -> Path:
        candidate = self.path / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise MediaWorkflowError("MEDIA_PATH_ESCAPE")
        try: candidate.resolve().relative_to(self.path.resolve())
        except (OSError, ValueError) as exc: raise MediaWorkflowError("MEDIA_PATH_ESCAPE") from exc
        if candidate.is_symlink(): raise MediaWorkflowError("MEDIA_SYMLINK_DENIED")
        return candidate

    def load(self) -> MediaProductJob:
        try:
            raw = json.loads(self.state_path.read_text("utf-8"))
            if raw.pop("schema_version") != SCHEMA_VERSION: raise ValueError
            raw["state"] = MediaWorkflowState(raw["state"])
            raw["intake_mode"] = IntakeMode(raw["intake_mode"])
            raw["completion_mode"] = CompletionMode(raw["completion_mode"])
            return MediaProductJob(**raw)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise MediaWorkflowError("MEDIA_JOB_STATE_INVALID") from exc

    def save(self, job: MediaProductJob) -> None:
        value = asdict(job); value["schema_version"] = SCHEMA_VERSION
        _atomic_json(self.state_path, value)

    def transition(self, target: MediaWorkflowState, *, reason: str, expected: MediaWorkflowState | None = None) -> MediaProductJob:
        job = self.load(); target = MediaWorkflowState(target)
        if expected is not None and job.state is not expected:
            raise MediaWorkflowError("MEDIA_STATE_CONFLICT")
        if target not in TRANSITIONS.get(job.state, set()):
            raise MediaWorkflowError(f"MEDIA_TRANSITION_INVALID:{job.state}:{target}")
        previous = job.state; job.state = target; job.updated_at = utc_now()
        job.history.append({"at": job.updated_at, "from": previous.value, "to": target.value, "reason": reason[:200]})
        self.save(job); return job

    def write_artifact(self, relative: str, value: bytes | str | dict | list) -> dict:
        path = self._inside(relative); path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if isinstance(value, (dict, list)): payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        elif isinstance(value, str): payload = value.encode()
        else: payload = value
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o600); os.replace(temporary, path)
        finally:
            try: os.unlink(temporary)
            except FileNotFoundError: pass
        job = self.load(); relative_path = str(path.relative_to(self.path)); digest = sha256_bytes(payload)
        job.content_hashes[relative_path] = digest; job.updated_at = utc_now(); self.save(job)
        return {"path": relative_path, "sha256": digest, "size_bytes": len(payload)}

    def read_artifact(self, relative: str) -> bytes:
        return self._inside(relative).read_bytes()

    def stage_upload(self, source: Path | str) -> dict:
        source = Path(source)
        if source.is_symlink() or not source.is_file() or source.suffix.lower() not in SUPPORTED_UPLOADS:
            raise MediaWorkflowError("MEDIA_UPLOAD_DENIED")
        if source.stat().st_size > 200 * 1024**2:
            raise MediaWorkflowError("MEDIA_UPLOAD_TOO_LARGE")
        target = f"source/{uuid.uuid4().hex[:12]}{source.suffix.lower()}"
        return self.write_artifact(target, source.read_bytes())

    def invalidate_candidate(self, reason: str) -> MediaProductJob:
        job = self.load(); job.candidate_revision += 1; job.approval = None; job.publish = None
        job.updated_at = utc_now(); job.history.append({"at": job.updated_at, "event": "candidate_invalidated", "reason": reason[:200]})
        self.save(job); return job


class EvidenceIntake:
    """Bounded URL/upload/brief intake; fetched content is always untrusted evidence."""
    def __init__(self, fetcher: SafeHttpFetcher | None = None): self.fetcher = fetcher or SafeHttpFetcher()

    def from_url(self, workspace: MediaWorkspace, url: str, *, category: str = "reference") -> dict:
        response = self.fetcher.fetch(url)
        text = response.body.decode("utf-8", errors="replace")
        record = Provenance(response.final_url, utc_now(), sha256_bytes(response.body), category, "supports_requirements")
        artifact = workspace.write_artifact(f"evidence/url-{record.content_sha256[:12]}.txt", text)
        return {"artifact": artifact, "provenance": asdict(record)}

    def from_brief(self, workspace: MediaWorkspace, brief: str) -> dict:
        if not brief.strip() or len(brief) > 100_000: raise MediaWorkflowError("MEDIA_BRIEF_INVALID")
        artifact = workspace.write_artifact("source/direct-brief.txt", brief.strip() + "\n")
        provenance = Provenance("owner-direct-brief", utc_now(), artifact["sha256"], "owner_brief", "authoritative_owner_input", "OWNER_PROVIDED")
        return {"artifact": artifact, "provenance": asdict(provenance)}


class RequirementsStore:
    REQUIRED = ("source_evidence.json", "requirements.json", "requirements.md", "production_brief.md")
    def persist(self, workspace: MediaWorkspace, requirements: Requirements, evidence: list[dict]) -> None:
        requirements.validate()
        workspace.write_artifact("source_evidence.json", {"schema_version": SCHEMA_VERSION, "items": evidence})
        value = asdict(requirements); value["schema_version"] = SCHEMA_VERSION
        workspace.write_artifact("requirements.json", value)
        lines = ["# Production Requirements", "", f"Objective: {requirements.objective}", f"Deliverables: {requirements.deliverable_count}",
                 f"Language: {requirements.language_requirements}", f"Duration: {requirements.duration_constraints or 'Not specified'}"]
        if requirements.uncertainties: lines += ["", "## Uncertainties", *[f"- {item}" for item in requirements.uncertainties]]
        workspace.write_artifact("requirements.md", "\n".join(lines) + "\n")
        workspace.write_artifact("production_brief.md", f"# Production Brief\n\n{requirements.objective}\n")


def new_media_workspace(task_name: str, owner_id: str, *, intake_mode=IntakeMode.DIRECT_BRIEF,
                        completion_mode=CompletionMode.AUTO_COMPLETE, root: Path | str = DEFAULT_ROOT) -> MediaWorkspace:
    identifier = f"{slugify(task_name)}-{uuid.uuid4().hex[:12]}"
    workspace = MediaWorkspace(identifier, root)
    workspace.create(owner_id=owner_id, task_name=task_name, intake_mode=IntakeMode(intake_mode), completion_mode=CompletionMode(completion_mode))
    return workspace
