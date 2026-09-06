#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import mlx_whisper


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zh", type=Path, required=True)
    parser.add_argument("--en", type=Path, required=True)
    parser.add_argument("--noisy", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    items = {
        "zh": args.zh,
        "en": args.en,
        "noisy": args.noisy,
    }
    result = {}

    for label, media in items.items():
        started = time.monotonic()
        payload = mlx_whisper.transcribe(
            str(media),
            path_or_hf_repo=str(args.model),
            word_timestamps=True,
            verbose=False,
        )
        result[label] = {
            "runtime_seconds": time.monotonic() - started,
            "payload": payload,
        }

    args.output.write_text(
        json.dumps(result, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
