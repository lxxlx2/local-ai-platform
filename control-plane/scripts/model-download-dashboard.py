#!/usr/bin/env python3
"""Compact read-only dashboard for the resumable model download queue.

Default mode is intentionally human-readable: one total line plus one line per
model, refreshed every ten minutes. Optional --details shows a few active local
partial/weight slices. The dashboard never starts, stops, signals, or mutates the
download manager.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
import time

SOURCE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = SOURCE_ROOT / "config/model-download-queue-v0.1.json"
DEFAULT_RUNTIME = Path("/Users/jerson/AI/runtime/model-downloads")
sys.path.insert(0, str(SOURCE_ROOT / "control-plane/src"))

from local_ai_control.services.model_downloads import load_queue_config, status_snapshot  # noqa: E402

GIB = 1024 ** 3
WEIGHT_SUFFIXES = {".safetensors", ".gguf", ".bin", ".pt", ".pth"}
STATE_LABELS = {
    "COMPLETED": "DONE",
    "DOWNLOADING": "GET ",
    "PENDING": "WAIT",
    "PAUSED": "PAUS",
    "RETRY_WAIT": "RETY",
    "FAILED": "FAIL",
}


def gib_short(value: int | float) -> str:
    return f"{float(value) / GIB:.2f}G"


def rate(value: float) -> str:
    if value <= 0:
        return "-"
    mib = value / (1024 ** 2)
    if mib >= 1024:
        return f"{mib / 1024:.2f}G/s"
    return f"{mib:.1f}M/s"


def eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, _sec = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return "<1m"


def bar(percent: float, width: int = 24) -> str:
    value = max(0.0, min(100.0, float(percent)))
    filled = int(round(width * value / 100.0))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def effective_bytes(row: dict) -> int:
    expected = int(row["expected_bytes"])
    if row["state"] == "COMPLETED":
        return expected
    return min(int(row["downloaded_bytes"]), int(expected * 0.999))


def activity_bytes(row: dict) -> int:
    """Bytes used only for activity/speed, without the 99.9% display clamp."""
    return int(row.get("downloaded_bytes") or 0)


def slices_for(root: Path) -> list[dict]:
    if not root.exists():
        return []
    result = []
    for item in root.rglob("*"):
        try:
            if not item.is_file() or item.is_symlink():
                continue
            rel = item.relative_to(root)
            partial = item.name.endswith(".incomplete")
            weight = ".cache" not in rel.parts and item.suffix.lower() in WEIGHT_SUFFIXES
            if not partial and not weight:
                continue
            stat = item.stat()
            result.append({
                "path": rel.as_posix(),
                "bytes": int(stat.st_size),
                "mtime": float(stat.st_mtime),
                "kind": "partial" if partial else "weight",
            })
        except (FileNotFoundError, OSError, ValueError):
            continue
    return sorted(result, key=lambda row: (-row["mtime"], -row["bytes"]))


def clear_screen() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")


def model_name(model_id: str) -> str:
    aliases = {
        "stt-whisper-large-v3": "Whisper",
        "tts-qwen3-base-bf16": "TTS Base",
        "tts-qwen3-voice-design-bf16": "TTS Voice",
        "image-flux2-klein-4b-bf16": "FLUX",
        "embed-qwen3-8b": "Embedding",
        "rerank-qwen3-8b": "Reranker",
        "raw-qwen38-27b-q6k": "RAW Qwen",
        "video-longcat-q8": "LongCat",
    }
    return aliases.get(model_id, model_id)[:18]


def render(state: dict, *, now: float, previous_time: float | None,
           previous_bytes: dict[str, int], details: bool) -> dict[str, int]:
    models = state["models"]
    total_expected = sum(int(row["expected_bytes"]) for row in models)
    total_effective = sum(effective_bytes(row) for row in models)
    overall_pct = 100.0 * total_effective / total_expected if total_expected else 0.0
    completed = sum(row["state"] == "COMPLETED" for row in models)
    dt = max(now - previous_time, 0.001) if previous_time is not None else None

    current_bytes: dict[str, int] = {}
    speeds: dict[str, float] = {}
    for row in models:
        current = activity_bytes(row)
        current_bytes[row["id"]] = current
        old = previous_bytes.get(row["id"], current)
        speeds[row["id"]] = max(current - old, 0) / dt if dt else 0.0

    global_speed = sum(speeds.values())
    remaining = max(total_expected - total_effective, 0)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    print(f"MODEL DOWNLOADS  {stamp}")
    print(
        f"Manager {state['manager_state']} | active {state['active_count']}/{state['parallel_limit']} | "
        f"PID {'OK' if state['manager_pid_verified'] else 'BAD'} | quarantine {state['quarantine_count']}"
    )
    print(
        f"TOTAL {bar(overall_pct)} {overall_pct:5.1f}%  "
        f"{gib_short(total_effective)}/{gib_short(total_expected)}  "
        f"left {gib_short(remaining)}  activity {rate(global_speed)}  "
        f"ETA~ {eta(remaining / global_speed if global_speed > 0 else None)}  done {completed}/{len(models)}"
    )
    print()

    finalizing_present = False
    for row in models:
        state_name = str(row["state"])
        name = model_name(row["id"])
        expected = int(row["expected_bytes"])
        current = effective_bytes(row)
        pct = 100.0 if state_name == "COMPLETED" else min(current / expected * 100.0, 99.9)
        speed_bps = speeds[row["id"]]
        remaining_model = max(expected - current, 0)
        worker = row.get("worker_pid")
        finalizing = state_name != "COMPLETED" and pct >= 99.9
        label = "FINAL" if finalizing else STATE_LABELS.get(state_name, state_name[:4])
        finalizing_present = finalizing_present or finalizing

        if state_name == "COMPLETED":
            print(f"{label:<5} {name:<18}  100.0%  {gib_short(expected):>8}")
        elif finalizing:
            if state_name == "DOWNLOADING":
                print(
                    f"{label:<5} {name:<18}   99.9%  "
                    f"{gib_short(current):>7}/{gib_short(expected):<7}  "
                    f"activity {rate(speed_bps):>8}  pid {worker or '-'}"
                )
            else:
                extra = f"  error={row['last_error_category']}" if row.get("last_error_category") else ""
                print(
                    f"{label:<5} {name:<18}   99.9%  "
                    f"{gib_short(current):>7}/{gib_short(expected):<7}  "
                    f"waiting verification{extra}"
                )
        elif state_name == "DOWNLOADING":
            print(
                f"{label:<5} {name:<18}  {pct:5.1f}%  "
                f"{gib_short(current):>7}/{gib_short(expected):<7}  "
                f"{rate(speed_bps):>8}  ETA {eta(remaining_model / speed_bps if speed_bps > 0 else None):>6}  "
                f"pid {worker or '-'}"
            )
        else:
            extra = f"  error={row['last_error_category']}" if row.get("last_error_category") else ""
            print(
                f"{label:<5} {name:<18}  {pct:5.1f}%  "
                f"{gib_short(current):>7}/{gib_short(expected):<7}{extra}"
            )

        if details and state_name == "DOWNLOADING":
            slices = slices_for(Path(row["local_dir"]))
            partials = [item for item in slices if item["kind"] == "partial"]
            weights = [item for item in slices if item["kind"] == "weight"]
            print(f"      slices: partial {len(partials)}, completed weights {len(weights)}")
            for item in slices[:4]:
                age = max(now - item["mtime"], 0)
                short_path = Path(item["path"]).name
                if len(short_path) > 54:
                    short_path = short_path[:24] + "..." + short_path[-24:]
                print(f"        {item['kind']:<7} {gib_short(item['bytes']):>7}  age {age/60:5.1f}m  {short_path}")

    if finalizing_present:
        print("\nFINAL = size boundary reached; waiting for downloader completion marker/verification.")
    print("Ctrl+C closes this view only. Downloads keep running.")
    return current_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description="Compact read-only model download dashboard")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--interval", type=float, default=600.0, help="refresh seconds; default 600 (10 minutes)")
    parser.add_argument("--details", action="store_true", help="show a few slices for active downloads")
    parser.add_argument("--all-slices", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 5 <= args.interval <= 86400:
        raise SystemExit("--interval must be between 5 and 86400 seconds")

    config = load_queue_config(Path(args.config).expanduser().resolve(strict=True))
    runtime = Path(args.runtime).expanduser().resolve()
    previous_time: float | None = None
    previous_bytes: dict[str, int] = {}
    details = bool(args.details or args.all_slices)

    try:
        while True:
            now = time.time()
            state = status_snapshot(config, runtime)
            clear_screen()
            previous_bytes = render(
                state,
                now=now,
                previous_time=previous_time,
                previous_bytes=previous_bytes,
                details=details,
            )
            sys.stdout.flush()
            previous_time = now
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
