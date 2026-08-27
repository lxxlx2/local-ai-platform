import json
import subprocess
import wave
from pathlib import Path

import pytest

from local_ai_control.services.presentation_tts import (
    DefaultVoiceBootstrap, MODEL_PINS, Qwen3TTSRuntime, SynthesisRequest, TTSRuntimeError,
)
from local_ai_control.services.presentation_voice import VoiceProfile, VoiceProfileStore, sha256_file


def model_snapshot(path: Path, kind: str):
    path.mkdir()
    (path / "model.bin").write_bytes(b"model")
    (path / ".local-ai-download-complete.json").write_text(json.dumps({
        **MODEL_PINS[kind], "files": [{"path": "model.bin", "size": 5}],
    }))


def write_wav(path: Path):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 3200)


def test_readiness_requires_exact_pinned_complete_snapshots(tmp_path):
    base = tmp_path / "base"; design = tmp_path / "design"
    model_snapshot(base, "base"); model_snapshot(design, "design")
    python = tmp_path / "python"; python.write_text("x"); python.chmod(0o700)
    worker = tmp_path / "worker.py"; worker.write_text("x")
    runtime = Qwen3TTSRuntime(audio_python=python, worker=worker, base_model=base, design_model=design)
    assert runtime.readiness()["ready"] is True
    marker = json.loads((base / ".local-ai-download-complete.json").read_text())
    marker["revision"] = "wrong"
    (base / ".local-ai-download-complete.json").write_text(json.dumps(marker))
    assert runtime.readiness()["base"] is False


def test_exact_shell_false_worker_invocation_and_output_validation(tmp_path):
    base = tmp_path / "base"; design = tmp_path / "design"
    model_snapshot(base, "base"); model_snapshot(design, "design")
    python = tmp_path / "python"; python.write_text("x"); python.chmod(0o700)
    worker = tmp_path / "worker.py"; worker.write_text("x")
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        request = json.loads((tmp_path / ".tts-request.json").read_text())
        write_wav(Path(request["requests"][0]["output"]))
        return subprocess.CompletedProcess(argv, 0, "", "")
    runtime = Qwen3TTSRuntime(audio_python=python, worker=worker, base_model=base, design_model=design, run=run)
    output = tmp_path / "out.wav"
    result = runtime.synthesize("design", [SynthesisRequest("hello", str(output), "en", instruction="voice")], tmp_path)
    assert len(result) == 1 and result[0].duration_seconds == pytest.approx(0.2)
    assert calls[0][0][:2] == [str(python), str(worker)]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["env"]["HF_HUB_OFFLINE"] == "1"
    assert not (tmp_path / ".tts-request.json").exists()


def test_worker_failure_and_output_escape_fail_closed(tmp_path):
    base = tmp_path / "base"; design = tmp_path / "design"
    model_snapshot(base, "base"); model_snapshot(design, "design")
    python = tmp_path / "python"; python.write_text("x"); python.chmod(0o700)
    worker = tmp_path / "worker.py"; worker.write_text("x")
    runtime = Qwen3TTSRuntime(
        audio_python=python, worker=worker, base_model=base, design_model=design,
        run=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 2, "", "denied"),
    )
    with pytest.raises(TTSRuntimeError, match="WORKER_FAILED"):
        runtime.synthesize("design", [SynthesisRequest("hello", str(tmp_path / "x.wav"), "en", instruction="x")], tmp_path)


def test_qualified_default_is_reused_without_voice_design(tmp_path):
    source = tmp_path / "source.wav"; write_wav(source)
    store = VoiceProfileStore(tmp_path / "profiles")
    profile = VoiceProfile(
        "0.1", "en-male-25-default", 1, "en", "test", 25, "male", "professional",
        "qwen3-tts-voice-design-reference", "model", "revision", "reference.wav",
        sha256_file(source), "anchor", "instruction", 16_000, 0.2, "QUALIFIED",
        "2026-08-28T00:00:00+00:00",
    )
    store.save(profile, source)
    class RuntimeMustNotRun:
        def synthesize(self, *args, **kwargs):
            raise AssertionError("qualified defaults must not be regenerated")
    assert DefaultVoiceBootstrap(RuntimeMustNotRun(), store).create("en-male-25-default") == profile
