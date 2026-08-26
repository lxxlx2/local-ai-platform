#!/usr/bin/env python3
"""Read-only live dashboard for the resumable model download queue.

Shows manager health, weighted total progress, per-model progress/speed/ETA,
and local payload/cache slices. It never starts, stops, signals, or mutates the
model download manager.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import os
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


def gib(value: int | float) -> str:
    return f"{float(value) / GIB:.3f} GiB"


def rate(value: float) -> str:
    if value <= 0:
        return "-"
    mib = value / (1024 ** 2)
    if mib >= 1024:
        return f"{mib / 1024:.2f} GiB/s"
    return f"{mib:.1f} MiB/s"


def eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {sec:02d}s"
    return f"{sec}s"


def bar(percent: float, width: int = 34) -> str:
    value = max(0.0, min(100.0, float(percent)))
    filled = int(round(width * value / 100.0))
    return "[" + "#" * filled + "." * (width - filled) + f"] {value:6.2f}%"


def effective_bytes(row: dict) -> int:
    expected = int(row["expected_bytes"])
    if row["state"] == "COMPLETED":
        return expected
    return min(int(row["downloaded_bytes"]), int(expected * 0.999))


def slice_files(root: Path) -> list[dict]:
    if not root.exists():
        return []
    rows: list[dict] = []
    for item in root.rglob("*"):
        try:
            if not item.is_file() or item.is_symlink():
                continue
            rel = item.relative_to(root)
            partial = item.name.endswith(".incomplete")
            payload_weight = ".cache" not in rel.parts and item.suffix.lower() in WEIGHT_SUFFIXES
            if not partial and not payload_weight:
                continue
            stat = item.stat()
            rows.append(
                {
                    "key": str(item),
                    "path": rel.as_posix(),
                    "bytes": int(stat.st_size),
                    "mtime": float(stat.st_mtime),
                    "kind": "PARTIAL" if partial else "PAYLOAD",
                }
            )
        except (FileNotFoundError, OSError, ValueError):
            continue
    return sorted(rows, key=lambda item: (item["kind"] != "PARTIAL", -item["mtime"], item["path"]))


def clear_screen() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")


def render(
    state: dict,
    *,
    now: float,
    previous_time: float | None,
    previous_model_bytes: dict[str, int],
    previous_slice_bytes: dict[str, int],
    slice_limit: int,
    all_slices: bool,
) -> tuple[dict[str, int], dict[str, int]]:
    models = state["models"]
    total_expected = sum(int(row["expected_bytes"]) for row in models)
    total_effective = sum(effective_bytes(row) for row in models)
    completed = sum(row["state"] == "COMPLETED" for row in models)
    dt = max(now - previous_time, 0.001) if previous_time is not None else None

    model_speeds: dict[str, float] = {}
    current_model_bytes: dict[str, int] = {}
    for row in models:
        current = effective_bytes(row)
        current_model_bytes[row["id"]] = current
        old = previous_model_bytes.get(row["id"], current)
        model_speeds[row["id"]] = max(current - old, 0) / dt if dt is not None else 0.0

    global_speed = sum(model_speeds.values())
    remaining = max(total_expected - total_effective, 0)
    overall_pct = 100.0 * total_effective / total_expected if total_expected else 0.0

    print("MODEL DOWNLOAD LIVE DASHBOARD")
    print(datetime.now().astimezone().strftime("updated: %Y-%m-%d %H:%M:%S %Z"))
    print(
        f"manager: {state['manager_state']}  pid={state['manager_pid'] or '-'}  "
        f"verified={'YES' if state['manager_pid_verified'] else 'NO'}  "
        f"active={state['active_count']}/{state['parallel_limit']}  "
        f"quarantine={state['quarantine_count']}"
    )
    print()
    print("TOTAL")
    print(bar(overall_pct, 42))
    print(
        f"effective: {gib(total_effective)} / {gib(total_expected)}  "
        f"remaining: {gib(remaining)}  completed: {completed}/{len(models)}  "
        f"speed: {rate(global_speed)}  ETA: {eta(remaining / global_speed if global_speed > 0 else None)}"
    )

    current_slice_bytes: dict[str, int] = {}
    for row in models:
        expected = int(row["expected_bytes"])
        current = effective_bytes(row)
        pct = 100.0 if row["state"] == "COMPLETED" else min(current / expected * 100.0, 99.9)
        speed_bps = model_speeds[row["id"]]
        model_remaining = max(expected - current, 0)
        model_eta = model_remaining / speed_bps if speed_bps > 0 else None
        print("\n" + "=" * 88)
        print(
            f"{row['id']}  state={row['state']}  worker={row['worker_pid'] or '-'}  "
            f"worker_verified={'YES' if row['worker_pid_verified'] else 'NO'}"
        )
        print(bar(pct))
        print(
            f"payload={gib(row['payload_bytes'])}  partial_cache={gib(row['partial_cache_bytes'])}  "
            f"effective={gib(current)}/{gib(expected)}  speed={rate(speed_bps)}  ETA={eta(model_eta)}"
        )
        if row.get("last_error_category"):
            print(f"last_error={row['last_error_category']}")

        root = Path(row["local_dir"])
        slices = slice_files(root)
        if not slices:
            if row["state"] not in {"COMPLETED"}:
                print("slices: no local weight/partial slices yet")
            continue

        partial_count = sum(item["kind"] == "PARTIAL" for item in slices)
        payload_count = sum(item["kind"] == "PAYLOAD" for item in slices)
        shown = slices if all_slices else slices[:slice_limit]
        print(f"slices: partial={partial_count} payload_weights={payload_count} showing={len(shown)}/{len(slices)}")
        for item in shown:
            current_slice_bytes[item["key"]] = item["bytes"]
            old = previous_slice_bytes.get(item["key"], item["bytes"])
            growth = max(item["bytes"] - old, 0) / dt if dt is not None else 0.0
            age = max(now - item["mtime"], 0)
            live = "LIVE" if item["kind"] == "PARTIAL" and age < 10 else item["kind"]
            print(
                f"  {live:7} {gib(item['bytes']):>11}  +{rate(growth):>11}  "
                f"age={age:5.1f}s  {item['path']}"
            )

    print("\nCtrl+C to exit. Dashboard is read-only; downloads continue in the manager.")
    return current_model_bytes, current_slice_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description="Live read-only dashboard for model downloads")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--slice-limit", type=int, default=12)
    parser.add_argument("--all-slices", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 0.5 <= args.interval <= 60:
        raise SystemExit("--interval must be between 0.5 and 60 seconds")
    if not 1 <= args.slice_limit <= 200:
        raise SystemExit("--slice-limit must be between 1 and 200")

    config = load_queue_config(Path(args.config).expanduser().resolve(strict=True))
    runtime = Path(args.runtime).expanduser().resolve()
    previous_time: float | None = None
    previous_model_bytes: dict[str, int] = {}
    previous_slice_bytes: dict[str, int] = {}

    try:
        while True:
            now = time.time()
            state = status_snapshot(config, runtime)
            clear_screen()
            previous_model_bytes, previous_slice_bytes = render(
                state,
                now=now,
                previous_time=previous_time,
                previous_model_bytes=previous_model_bytes,
                previous_slice_bytes=previous_slice_bytes,
                slice_limit=args.slice_limit,
                all_slices=args.all_slices,
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
