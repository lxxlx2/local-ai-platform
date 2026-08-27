"""Deterministic language routing and private persistent voice profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import wave
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


Language = Literal["zh", "en", "mixed", "unknown"]
PROFILE_STATUSES = {
    "MISSING", "GENERATED_NOT_QUALIFIED", "QUALIFIED", "INVALID", "UNHEALTHY"
}
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
DEFAULT_PROFILE_BY_LANGUAGE = {
    "zh": "zh-male-25-default",
    "en": "en-male-25-default",
}


class VoiceProfileError(RuntimeError):
    """A profile or deterministic routing policy failed closed."""


@dataclass(frozen=True)
class LanguageDecision:
    language: Language
    dominant_language: str | None
    han_characters: int
    latin_characters: int
    warning: str | None = None


class LanguageDetector:
    """Small deterministic detector for presentation narration."""

    def detect(self, text: str) -> LanguageDecision:
        bounded = text[:200_000]
        han = sum(1 for char in bounded if "\u3400" <= char <= "\u9fff")
        latin = sum(1 for char in bounded if char.isascii() and char.isalpha())
        meaningful = han + latin
        if meaningful < 3:
            return LanguageDecision("unknown", None, han, latin)
        if han and latin and min(han, latin) / meaningful >= 0.12:
            dominant = "zh" if han >= latin else "en"
            return LanguageDecision(
                "mixed", dominant, han, latin,
                f"MIXED_LANGUAGE_USING_{dominant.upper()}_DOMINANT_PROFILE",
            )
        if han > latin:
            return LanguageDecision("zh", "zh", han, latin)
        return LanguageDecision("en", "en", han, latin)


@dataclass(frozen=True)
class VoiceProfile:
    schema_version: str
    profile_id: str
    profile_revision: int
    language: str
    description: str
    age: int | None
    gender: str
    style: str
    backend: str
    model_identity: str
    model_revision: str
    reference_path: str
    reference_sha256: str
    reference_transcript: str
    design_instruction: str
    sample_rate: int
    duration_seconds: float
    qualification_status: str
    qualified_at: str | None
    future_tuned_model: str | None = None

    @classmethod
    def from_dict(cls, value: dict) -> "VoiceProfile":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise VoiceProfileError("VOICE_PROFILE_SCHEMA_INVALID")
        profile = cls(**value)
        profile.validate_metadata()
        return profile

    def validate_metadata(self) -> None:
        if self.schema_version != "0.1" or not PROFILE_ID_RE.fullmatch(self.profile_id):
            raise VoiceProfileError("VOICE_PROFILE_SCHEMA_INVALID")
        if self.profile_revision < 1 or self.language not in {"zh", "en"}:
            raise VoiceProfileError("VOICE_PROFILE_SCHEMA_INVALID")
        if self.qualification_status not in PROFILE_STATUSES:
            raise VoiceProfileError("VOICE_PROFILE_SCHEMA_INVALID")
        if self.backend not in {"qwen3-tts-voice-design-reference", "owner-reference"}:
            raise VoiceProfileError("VOICE_PROFILE_BACKEND_INVALID")
        if not re.fullmatch(r"[a-f0-9]{64}", self.reference_sha256):
            raise VoiceProfileError("VOICE_PROFILE_HASH_INVALID")
        if self.sample_rate < 8_000 or self.sample_rate > 192_000:
            raise VoiceProfileError("VOICE_PROFILE_AUDIO_INVALID")
        if not (0.1 <= self.duration_seconds <= 120.0):
            raise VoiceProfileError("VOICE_PROFILE_AUDIO_INVALID")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_wav(path: Path) -> tuple[int, float]:
    if path.is_symlink() or not path.is_file():
        raise VoiceProfileError("VOICE_REFERENCE_INVALID")
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.getnframes()
            compression = wav.getcomptype()
    except (wave.Error, EOFError, OSError) as exc:
        raise VoiceProfileError("VOICE_REFERENCE_INVALID") from exc
    duration = frames / rate if rate else 0.0
    if channels not in {1, 2} or sample_width not in {2, 3, 4} or compression != "NONE":
        raise VoiceProfileError("VOICE_REFERENCE_FORMAT_UNSUPPORTED")
    if rate < 8_000 or rate > 192_000 or not (0.1 <= duration <= 120.0):
        raise VoiceProfileError("VOICE_REFERENCE_INVALID")
    return rate, duration


class VoiceProfileStore:
    """Strict private profile store. Assets never resolve outside its root."""

    def __init__(self, root: Path | str = "/Users/jerson/AI/runtime/voice-profiles"):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        if self.root.is_symlink():
            raise VoiceProfileError("VOICE_PROFILE_ROOT_INVALID")

    def _profile_dir(self, profile_id: str) -> Path:
        if not PROFILE_ID_RE.fullmatch(profile_id):
            raise VoiceProfileError("VOICE_PROFILE_ID_INVALID")
        path = self.root / profile_id
        if path.exists() and path.is_symlink():
            raise VoiceProfileError("VOICE_PROFILE_SYMLINK_DENIED")
        return path

    def load(self, profile_id: str, *, require_qualified: bool = True) -> VoiceProfile:
        directory = self._profile_dir(profile_id)
        metadata_path = directory / "profile.json"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise VoiceProfileError("VOICE_PROFILE_MISSING")
        try:
            profile = VoiceProfile.from_dict(json.loads(metadata_path.read_text("utf-8")))
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            raise VoiceProfileError("VOICE_PROFILE_INVALID") from exc
        if profile.profile_id != profile_id or profile.reference_path != "reference.wav":
            raise VoiceProfileError("VOICE_PROFILE_ASSET_INVALID")
        reference = directory / profile.reference_path
        resolved_root = self.root.resolve()
        try:
            if reference.resolve().parent != directory.resolve() or directory.resolve().parent != resolved_root:
                raise VoiceProfileError("VOICE_PROFILE_ASSET_ESCAPE")
        except OSError as exc:
            raise VoiceProfileError("VOICE_PROFILE_ASSET_INVALID") from exc
        rate, duration = validate_wav(reference)
        if sha256_file(reference) != profile.reference_sha256:
            raise VoiceProfileError("VOICE_PROFILE_HASH_MISMATCH")
        if rate != profile.sample_rate or abs(duration - profile.duration_seconds) > 0.02:
            raise VoiceProfileError("VOICE_PROFILE_AUDIO_MISMATCH")
        if require_qualified and profile.qualification_status != "QUALIFIED":
            raise VoiceProfileError("VOICE_PROFILE_NOT_QUALIFIED")
        return profile

    def save(self, profile: VoiceProfile, reference_source: Path, *, overwrite: bool = False) -> VoiceProfile:
        profile.validate_metadata()
        directory = self._profile_dir(profile.profile_id)
        if directory.exists() and not overwrite:
            raise VoiceProfileError("VOICE_PROFILE_ALREADY_EXISTS")
        rate, duration = validate_wav(reference_source)
        digest = sha256_file(reference_source)
        if (rate, round(duration, 3), digest) != (
            profile.sample_rate, round(profile.duration_seconds, 3), profile.reference_sha256
        ):
            raise VoiceProfileError("VOICE_PROFILE_SOURCE_MISMATCH")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        reference = directory / "reference.wav"
        temp_reference = directory / ".reference.wav.tmp"
        with reference_source.open("rb") as source, temp_reference.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temp_reference, 0o600)
        os.replace(temp_reference, reference)
        self._atomic_json(directory / "profile.json", asdict(profile))
        return self.load(profile.profile_id, require_qualified=False)

    @staticmethod
    def _atomic_json(path: Path, value: dict) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".profile-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class VoiceRouter:
    def __init__(self, store: VoiceProfileStore, detector: LanguageDetector | None = None):
        self.store = store
        self.detector = detector or LanguageDetector()

    def route(
        self, text: str, *, language: str = "auto", profile_id: str = "auto"
    ) -> tuple[LanguageDecision, VoiceProfile]:
        if language not in {"auto", "zh", "en"}:
            raise VoiceProfileError("LANGUAGE_OVERRIDE_INVALID")
        decision = self.detector.detect(text)
        selected_language = language if language != "auto" else decision.dominant_language
        if profile_id != "auto":
            profile = self.store.load(profile_id)
            if selected_language and profile.language != selected_language:
                raise VoiceProfileError("VOICE_PROFILE_LANGUAGE_MISMATCH")
            return decision, profile
        if selected_language not in DEFAULT_PROFILE_BY_LANGUAGE:
            raise VoiceProfileError("LANGUAGE_UNKNOWN_OVERRIDE_REQUIRED")
        return decision, self.store.load(DEFAULT_PROFILE_BY_LANGUAGE[selected_language])


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
