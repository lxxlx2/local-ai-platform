#!/usr/bin/env python3
"""Narrow offline MLX Audio worker. Accepts only a validated request file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


MODELS = {
    "design": Path("/Users/jerson/AI/models/qwen3-tts-voice-design-bf16"),
    "clone": Path("/Users/jerson/AI/models/qwen3-tts-base-bf16"),
}
LOCAL_STT_MODEL = Path("/Users/jerson/AI/models/whisper-large-v3-mlx")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request_path = Path(args.request)
    if request_path.is_symlink() or not request_path.is_file():
        fail("TTS_REQUEST_INVALID")
    request_root = request_path.parent.resolve()
    try:
        payload = json.loads(request_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("TTS_REQUEST_INVALID")
    if set(payload) != {"schema_version", "mode", "model", "requests"} or payload["schema_version"] != "0.1":
        fail("TTS_REQUEST_SCHEMA_INVALID")
    mode = payload["mode"]
    if mode not in MODELS or Path(payload["model"]) != MODELS[mode]:
        fail("TTS_MODEL_DENIED")
    requests = payload["requests"]
    if not isinstance(requests, list) or not 1 <= len(requests) <= 200:
        fail("TTS_REQUEST_COUNT_INVALID")
    from mlx_audio.tts.generate import generate_audio
    from mlx_audio.tts.utils import load_model
    model = load_model(MODELS[mode])
    for index, item in enumerate(requests):
        expected = {"text", "output", "language", "reference_audio", "reference_text", "instruction"}
        if not isinstance(item, dict) or set(item) != expected:
            fail("TTS_ITEM_SCHEMA_INVALID")
        text = item["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 10_000:
            fail("TTS_TEXT_INVALID")
        if item["language"] not in {"zh", "en"}:
            fail("TTS_LANGUAGE_INVALID")
        output = Path(item["output"])
        try:
            if output.is_symlink() or output.resolve().parent != request_root:
                fail("TTS_OUTPUT_DENIED")
        except OSError:
            fail("TTS_OUTPUT_DENIED")
        if output.suffix.lower() != ".wav":
            fail("TTS_OUTPUT_FORMAT_DENIED")
        kwargs = {
            "model": model, "text": text, "lang_code": item["language"],
            "output_path": str(request_root), "file_prefix": output.stem,
            "audio_format": "wav", "join_audio": True, "verbose": False,
            "temperature": 0.7, "max_tokens": 1800,
        }
        if mode == "design":
            if not isinstance(item["instruction"], str) or not item["instruction"].strip():
                fail("TTS_DESIGN_INSTRUCTION_REQUIRED")
            kwargs["instruct"] = item["instruction"]
        else:
            reference = Path(item["reference_audio"] or "")
            try:
                allowed_profile_root = Path("/Users/jerson/AI/runtime/voice-profiles").resolve()
                allowed_job_root = Path("/Users/jerson/AI/runtime/presentation-jobs").resolve()
                resolved = reference.resolve()
                if reference.is_symlink() or not reference.is_file() or not (
                    resolved.is_relative_to(allowed_profile_root) or resolved.is_relative_to(allowed_job_root)
                ):
                    fail("TTS_REFERENCE_DENIED")
            except OSError:
                fail("TTS_REFERENCE_DENIED")
            kwargs["ref_audio"] = str(reference)
            if isinstance(item["reference_text"], str) and item["reference_text"].strip():
                kwargs["ref_text"] = item["reference_text"]
                kwargs["stt_model"] = None
            else:
                marker = LOCAL_STT_MODEL / ".local-ai-download-complete.json"
                try:
                    stt_marker = json.loads(marker.read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    stt_marker = {}
                if (LOCAL_STT_MODEL.is_symlink() or
                        stt_marker.get("repo") != "mlx-community/whisper-large-v3-mlx" or
                        stt_marker.get("revision") != "49e6aa286ad60c14352c404340ded53710378a11"):
                    fail("LOCAL_STT_REFERENCE_TRANSCRIPTION_UNAVAILABLE")
                kwargs["ref_text"] = None
                kwargs["stt_model"] = str(LOCAL_STT_MODEL)
        generate_audio(**kwargs)
        if not output.is_file() or output.stat().st_size < 100:
            fail(f"TTS_OUTPUT_MISSING:{index}")
        os.chmod(output, 0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
