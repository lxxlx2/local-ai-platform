"""Local-only Qwen3-TTS runtime and persistent default voice bootstrap."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .presentation_voice import (
    VoiceProfile, VoiceProfileError, VoiceProfileStore, sha256_file, utc_now, validate_wav,
    validate_wav_quality,
)


BASE_MODEL = Path("/Users/jerson/AI/models/qwen3-tts-base-bf16")
DESIGN_MODEL = Path("/Users/jerson/AI/models/qwen3-tts-voice-design-bf16")
AUDIO_PYTHON = Path("/Users/jerson/AI/runtime/audio-venv/bin/python")
DEPLOYED_WORKER = Path("/Users/jerson/AI/control-plane/scripts/presentation-tts-worker.py")

MODEL_PINS = {
    "base": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",
        "revision": "a6eb4f68e4b056f1215157bb696209bc82a6db48",
    },
    "design": {
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16",
        "revision": "7d3824abff87e49756bb0f83fb5411de75d160c4",
    },
}

DEFAULT_VOICES = {
    "zh-male-25-default": {
        "language": "zh", "description": "约25岁男性，标准普通话，年轻自然、清晰专业",
        "instruction": "一名大约25岁的男性，标准普通话，声音年轻自然，清晰专业，语速中等，表达自信但不过度夸张，适合大学、科技和商务演示。",
        "anchor": "欢迎观看本次演示。接下来，我将用清晰简洁的方式介绍主要内容。",
        "qualification_text": "欢迎观看本次演示。接下来，我们将介绍系统的主要功能。",
    },
    "en-male-25-default": {
        "language": "en", "description": "Male around 25, neutral international English, young and professional",
        "instruction": "A male presenter around 25 years old with a clear neutral international English accent. Young, natural, professional and confident, with a medium speaking pace and no exaggerated dramatic delivery. Suitable for university, technology and business presentations.",
        "anchor": "Welcome to this presentation. I will explain the key ideas clearly and guide you through each section.",
        "qualification_text": "Welcome to this presentation. We will now introduce the main features of the system.",
    },
}


class TTSRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SynthesisRequest:
    text: str
    output: str
    language: str
    reference_audio: str | None = None
    reference_text: str | None = None
    instruction: str | None = None


@dataclass(frozen=True)
class SynthesisArtifact:
    path: str
    sha256: str
    sample_rate: int
    duration_seconds: float
    size_bytes: int


class Qwen3TTSRuntime:
    """Runs fixed local models through an exact, shell-free worker invocation."""

    def __init__(
        self, *, audio_python: Path = AUDIO_PYTHON, worker: Path = DEPLOYED_WORKER,
        base_model: Path = BASE_MODEL, design_model: Path = DESIGN_MODEL,
        run=subprocess.run,
    ):
        self.audio_python = Path(audio_python)
        self.worker = Path(worker)
        self.base_model = Path(base_model)
        self.design_model = Path(design_model)
        self.run = run

    def readiness(self) -> dict:
        result = {
            "audio_python": self.audio_python.is_file() and os.access(self.audio_python, os.X_OK),
            "worker": self.worker.is_file() and not self.worker.is_symlink(),
            "base": self._snapshot_valid(self.base_model, MODEL_PINS["base"]),
            "design": self._snapshot_valid(self.design_model, MODEL_PINS["design"]),
            "cloud_fallback": False,
        }
        result["ready"] = all(result[key] for key in ("audio_python", "worker", "base", "design"))
        return result

    @staticmethod
    def _snapshot_valid(path: Path, pin: dict) -> bool:
        marker = path / ".local-ai-download-complete.json"
        if path.is_symlink() or not marker.is_file() or marker.is_symlink():
            return False
        try:
            value = json.loads(marker.read_text("utf-8"))
            if value.get("repo") != pin["repo"] or value.get("revision") != pin["revision"]:
                return False
            for entry in value.get("files", []):
                relative = Path(entry["path"])
                candidate = path / relative
                if relative.is_absolute() or ".." in relative.parts or candidate.is_symlink():
                    return False
                if not candidate.is_file() or candidate.stat().st_size != entry["size"]:
                    return False
            return bool(value.get("files"))
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return False

    def synthesize(self, mode: str, requests: list[SynthesisRequest], request_root: Path) -> list[SynthesisArtifact]:
        if mode not in {"design", "clone"} or not requests:
            raise TTSRuntimeError("TTS_REQUEST_INVALID")
        ready = self.readiness()
        if not ready["ready"]:
            raise TTSRuntimeError(f"TTS_RUNTIME_NOT_READY:{json.dumps(ready, sort_keys=True)}")
        request_root = request_root.resolve()
        if request_root.is_symlink() or not request_root.is_dir():
            raise TTSRuntimeError("TTS_REQUEST_ROOT_INVALID")
        request_file = request_root / ".tts-request.json"
        payload = {
            "schema_version": "0.1", "mode": mode,
            "model": str(self.design_model if mode == "design" else self.base_model),
            "requests": [asdict(item) for item in requests],
        }
        request_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(request_file, 0o600)
        env = {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin", "HOME": str(Path.home()),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"), "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1", "NO_PROXY": "*",
        }
        try:
            completed = self.run(
                [str(self.audio_python), str(self.worker), "--request", str(request_file)],
                cwd=str(request_root), env=env, shell=False, capture_output=True, text=True,
                timeout=1800, check=False,
            )
        finally:
            request_file.unlink(missing_ok=True)
        if completed.returncode != 0:
            category = (completed.stderr or completed.stdout or "TTS_WORKER_FAILED")[-500:]
            raise TTSRuntimeError(f"TTS_WORKER_FAILED:{category}")
        artifacts = []
        for request in requests:
            output = Path(request.output)
            try:
                if output.resolve().parent != request_root:
                    raise TTSRuntimeError("TTS_OUTPUT_ESCAPE")
            except OSError as exc:
                raise TTSRuntimeError("TTS_OUTPUT_INVALID") from exc
            rate, duration = validate_wav(output)
            artifacts.append(SynthesisArtifact(
                str(output), sha256_file(output), rate, duration, output.stat().st_size,
            ))
        return artifacts


class DefaultVoiceBootstrap:
    def __init__(self, runtime: Qwen3TTSRuntime, store: VoiceProfileStore):
        self.runtime = runtime
        self.store = store

    def create(self, profile_id: str, *, replace_existing: bool = False) -> VoiceProfile:
        if profile_id not in DEFAULT_VOICES:
            raise VoiceProfileError("DEFAULT_VOICE_ID_INVALID")
        if not replace_existing:
            try:
                return self.store.load(profile_id)
            except VoiceProfileError as exc:
                if str(exc) not in {"VOICE_PROFILE_MISSING", "VOICE_PROFILE_NOT_QUALIFIED"}:
                    raise
                if str(exc) == "VOICE_PROFILE_NOT_QUALIFIED":
                    raise VoiceProfileError("DEFAULT_VOICE_REVIEW_REQUIRED") from exc
        spec = DEFAULT_VOICES[profile_id]
        with tempfile.TemporaryDirectory(prefix="voice-bootstrap-", dir=self.store.root) as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            self.runtime.synthesize("design", [SynthesisRequest(
                spec["anchor"], str(reference), spec["language"], instruction=spec["instruction"],
            )], root)
            quality = validate_wav_quality(reference)
            rate, duration = quality["sample_rate"], quality["duration_seconds"]
            candidate = VoiceProfile(
                "0.1", profile_id, 1, spec["language"], spec["description"], 25, "male",
                "young-natural-professional-medium-pace", "qwen3-tts-voice-design-reference",
                MODEL_PINS["design"]["repo"], MODEL_PINS["design"]["revision"],
                "reference.wav", sha256_file(reference), spec["anchor"], spec["instruction"],
                rate, duration, "GENERATED_NOT_QUALIFIED", None,
            )
            self.store.save(candidate, reference, overwrite=replace_existing)
        return self.qualify(profile_id)

    def qualify(self, profile_id: str) -> VoiceProfile:
        if profile_id not in DEFAULT_VOICES:
            raise VoiceProfileError("DEFAULT_VOICE_ID_INVALID")
        spec = DEFAULT_VOICES[profile_id]
        # A real Base-clone smoke is the deterministic machine qualification gate.
        candidate = self.store.load(profile_id, require_qualified=False)
        if candidate.qualification_status == "QUALIFIED":
            return candidate
        profile_dir = self.store.root / profile_id
        qualification = profile_dir / ".qualification.wav"
        self.runtime.synthesize("clone", [SynthesisRequest(
            spec["qualification_text"], str(qualification), spec["language"],
            reference_audio=str(profile_dir / "reference.wav"), reference_text=spec["anchor"],
        )], profile_dir)
        validate_wav_quality(qualification)
        qualification.unlink(missing_ok=True)
        qualified = replace(candidate, qualification_status="QUALIFIED", qualified_at=utc_now())
        return self.store.save(qualified, profile_dir / "reference.wav", overwrite=True)

    def create_defaults(self) -> dict[str, VoiceProfile]:
        return {profile_id: self.create(profile_id) for profile_id in DEFAULT_VOICES}
