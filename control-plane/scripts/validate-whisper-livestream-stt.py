#!/usr/bin/env python3
"""One-shot direct-work validation for Whisper large-v3 on real media.

This harness validates the LIVESTREAM_STT workflow under representative desktop
load. It never closes user applications, never touches unrelated fixed ports,
never installs packages, and only terminates the exact child process it starts.

Required inputs:
- one real Chinese speech clip
- one real English speech clip
- one real noisy/mixed clip representative of the clipping workflow

Private media and full transcripts stay under runtime/direct-work-validation and
are never written into Git by this script. A safe Git summary is emitted at the
end for later synchronization.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any

ROOT = Path("/Users/jerson/AI")
MODEL_PATH = ROOT / "models/whisper-large-v3-mlx"
AUDIO_PYTHON = ROOT / "runtime/audio-venv/bin/python"
RUNTIME_ROOT = ROOT / "runtime/direct-work-validation/whisper-large-v3"
PROFILE_ID = "whisper-large-v3"
MODEL_ID = "mlx-community/whisper-large-v3-mlx"
WORKFLOW_ID = "LIVESTREAM_STT"
EXPECTED_MEMORY_GIB = 6.0
SWAP_GROWTH_LIMIT_GIB = 2.0
ABSOLUTE_SWAP_LIMIT_GIB = 6.0

sys.path.insert(0, str(ROOT / "control-plane/src"))

from local_ai_control.services.models import MemoryPreflight  # noqa: E402
from local_ai_control.services.workload_admission import (  # noqa: E402
    WorkloadClass,
    WorkloadManifestProbe,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def ffprobe_duration(path: Path) -> float:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        timeout=30,
    ).strip()
    value = float(raw)
    if value <= 0:
        raise ValueError("media duration is not positive")
    return value


def format_ts(seconds: float) -> str:
    millis = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def segments_valid(segments: list[dict[str, Any]]) -> bool:
    if not segments:
        return False
    previous_end = -1.0
    for segment in segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except Exception:
            return False
        if start < 0 or end < start or start + 0.001 < previous_end:
            return False
        previous_end = end
    return True


def render_text(result: dict[str, Any]) -> str:
    segments = result.get("segments") or []
    lines = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        lines.append(
            f"[{float(segment.get('start', 0.0)):.2f}-{float(segment.get('end', 0.0)):.2f}] {text}"
        )
    return "\n".join(lines).strip() + ("\n" if lines else "")


def render_srt(result: dict[str, Any]) -> str:
    segments = result.get("segments") or []
    out = []
    index = 1
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        out.extend(
            [
                str(index),
                f"{format_ts(float(segment.get('start', 0.0)))} --> {format_ts(float(segment.get('end', 0.0)))}",
                text,
                "",
            ]
        )
        index += 1
    return "\n".join(out)


def yes_no(prompt: str) -> bool:
    while True:
        answer = input(prompt).strip().lower()
        if answer in {"y", "yes", "1", "true"}:
            return True
        if answer in {"n", "no", "0", "false"}:
            return False
        print("Please enter y or n.")


def terminate_owned(process: subprocess.Popen[Any]) -> bool:
    if process.poll() is not None:
        return True
    pgid = os.getpgid(process.pid)
    if pgid != process.pid:
        raise RuntimeError("owned child is not process-group leader")
    os.killpg(pgid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
        return True
    except subprocess.TimeoutExpired:
        os.killpg(pgid, signal.SIGKILL)
        process.wait(timeout=10)
        return True


class ResourceMonitor:
    def __init__(self, process: subprocess.Popen[Any], baseline_swap_gib: float):
        self.process = process
        self.baseline_swap_gib = baseline_swap_gib
        self.samples: list[dict[str, Any]] = []
        self.violation: str | None = None
        self.warning_observed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._probe = MemoryPreflight().probe
        self._started = time.monotonic()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="whisper-direct-work-resource-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.is_set() and self.process.poll() is None:
            try:
                snapshot = self._probe()
                delta = snapshot.swap_used_gib - self.baseline_swap_gib
                self.samples.append(
                    {
                        "timestamp": snapshot.timestamp,
                        "elapsed_seconds": round(time.monotonic() - self._started, 3),
                        "pressure": snapshot.pressure,
                        "available_gib": snapshot.available_gib,
                        "reclaimable_gib": snapshot.reclaimable_gib,
                        "compressed_gib": snapshot.compressed_gib,
                        "swap_used_gib": snapshot.swap_used_gib,
                        "swap_delta_gib": delta,
                    }
                )
                if snapshot.pressure == "WARNING":
                    self.warning_observed = True
                if snapshot.pressure == "CRITICAL":
                    self.violation = "MEMORY_PRESSURE_CRITICAL"
                elif delta > SWAP_GROWTH_LIMIT_GIB:
                    self.violation = "RELATIVE_SWAP_GROWTH_LIMIT"
                elif snapshot.swap_used_gib > ABSOLUTE_SWAP_LIMIT_GIB:
                    self.violation = "ABSOLUTE_SWAP_LIMIT"
                if self.violation:
                    terminate_owned(self.process)
                    return
            except Exception:
                self.violation = "RESOURCE_OBSERVATION_FAILED"
                if self.process.poll() is None:
                    try:
                        terminate_owned(self.process)
                    except Exception:
                        pass
                return
            self._stop.wait(1.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Whisper large-v3 against real livestream/video transcription work."
    )
    parser.add_argument("--zh", type=Path, required=True, help="Real Chinese speech clip")
    parser.add_argument("--en", type=Path, required=True, help="Real English speech clip")
    parser.add_argument("--noisy", type=Path, required=True, help="Real noisy/mixed workflow clip")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip Owner quality questions; result remains NOT_TESTED until manual review.",
    )
    return parser.parse_args()


def dependency_failure(output_dir: Path, reason: str, started_at: str) -> int:
    result = {
        "schema_version": "0.1",
        "profile_id": PROFILE_ID,
        "model_id": MODEL_ID,
        "workflow_id": WORKFLOW_ID,
        "workload_class": "REPRESENTATIVE_WORKLOAD",
        "deployment_mode": "ONE_SHOT_BATCH",
        "started_at": started_at,
        "finished_at": utc_now(),
        "final_status": "FUNCTIONAL_FAIL",
        "reason": reason,
        "model_started": False,
        "cleanup_ok": True,
    }
    write_json(output_dir / "result.json", result)
    print(json.dumps({"result_dir": str(output_dir), **result}, ensure_ascii=False, indent=2))
    return 3


def main() -> int:
    args = parse_args()
    started_at = utc_now()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = RUNTIME_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(output_dir, 0o700)

    print("===== WHISPER LIVESTREAM_STT DIRECT-WORK VALIDATION =====")
    print(f"result_dir={output_dir}")
    print("user_apps_closed_by_harness=false")
    print("unrelated_ports_accessed=false")
    print("packages_installed=false")
    print()

    inputs = {"zh": args.zh.expanduser().resolve(), "en": args.en.expanduser().resolve(), "noisy": args.noisy.expanduser().resolve()}
    for label, path in inputs.items():
        if not path.is_file():
            return dependency_failure(output_dir, f"INPUT_MISSING:{label}", started_at)

    if not MODEL_PATH.is_dir():
        return dependency_failure(output_dir, "MODEL_PATH_MISSING", started_at)
    if not AUDIO_PYTHON.is_file():
        return dependency_failure(output_dir, "AUDIO_VENV_PYTHON_MISSING", started_at)
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return dependency_failure(output_dir, "FFMPEG_OR_FFPROBE_MISSING", started_at)

    import_check = subprocess.run(
        [str(AUDIO_PYTHON), "-c", "import mlx_whisper; print('MLX_WHISPER_OK')"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if import_check.returncode != 0 or "MLX_WHISPER_OK" not in import_check.stdout:
        (output_dir / "dependency-check.stderr.txt").write_text(import_check.stderr[-4000:], encoding="utf-8")
        return dependency_failure(output_dir, "MLX_WHISPER_IMPORT_FAILED", started_at)

    manifest = WorkloadManifestProbe(ports=(), top_n=50).capture(WorkloadClass.REPRESENTATIVE_WORKLOAD)
    write_json(output_dir / "workload-manifest.json", manifest.to_dict())

    preflight = MemoryPreflight().check(EXPECTED_MEMORY_GIB)
    write_json(output_dir / "preflight.json", asdict(preflight))
    print("===== PREFLIGHT =====")
    print(f"allowed={preflight.allowed}")
    print(f"reason={preflight.reason}")
    print(f"reclaimable_gib={preflight.snapshot.reclaimable_gib}")
    print(f"swap_used_gib={preflight.snapshot.swap_used_gib}")
    print(f"pressure={preflight.snapshot.pressure}")
    print()

    if not preflight.allowed:
        result = {
            "schema_version": "0.1",
            "profile_id": PROFILE_ID,
            "model_id": MODEL_ID,
            "workflow_id": WORKFLOW_ID,
            "workload_class": manifest.workload_class.value,
            "deployment_mode": "ONE_SHOT_BATCH",
            "started_at": started_at,
            "finished_at": utc_now(),
            "final_status": "RESOURCE_BLOCKED",
            "reason": f"RESOURCE_PREFLIGHT_DENIED:{preflight.reason}",
            "model_started": False,
            "cleanup_ok": True,
        }
        write_json(output_dir / "result.json", result)
        print(json.dumps({"result_dir": str(output_dir), **result}, ensure_ascii=False, indent=2))
        return 2

    media_meta: dict[str, Any] = {}
    for label, path in inputs.items():
        media_meta[label] = {
            "basename": path.name,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "duration_seconds": ffprobe_duration(path),
        }
    write_json(output_dir / "input-metadata.json", media_meta)

    helper_path = output_dir / "_whisper_worker.py"
    worker_json = output_dir / "worker-output.json"
    worker_log = output_dir / "worker.log"
    helper_path.write_text(
        """from __future__ import annotations\n"
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "import mlx_whisper\n"
        "model = sys.argv[1]\n"
        "output = Path(sys.argv[2])\n"
        "items = {'zh': sys.argv[3], 'en': sys.argv[4], 'noisy': sys.argv[5]}\n"
        "result = {}\n"
        "for label, media in items.items():\n"
        "    started = time.monotonic()\n"
        "    payload = mlx_whisper.transcribe(media, path_or_hf_repo=model, word_timestamps=True, verbose=False)\n"
        "    result[label] = {'runtime_seconds': time.monotonic()-started, 'payload': payload}\n"
        "output.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')\n"
        """,
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"

    command = [
        str(AUDIO_PYTHON),
        str(helper_path),
        str(MODEL_PATH),
        str(worker_json),
        str(inputs["zh"]),
        str(inputs["en"]),
        str(inputs["noisy"]),
    ]

    process: subprocess.Popen[Any] | None = None
    monitor: ResourceMonitor | None = None
    cleanup_ok = False
    worker_returncode: int | None = None
    functional_reason = "UNCLASSIFIED"

    try:
        with worker_log.open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT),
                env=env,
                start_new_session=True,
            )
            if os.getpgid(process.pid) != process.pid:
                raise RuntimeError("worker process-group ownership proof failed")
            monitor = ResourceMonitor(process, preflight.snapshot.swap_used_gib)
            monitor.start()
            worker_returncode = process.wait(timeout=3600)
    except subprocess.TimeoutExpired:
        functional_reason = "WORKER_TIMEOUT_3600S"
        if process and process.poll() is None:
            terminate_owned(process)
    except KeyboardInterrupt:
        functional_reason = "OWNER_INTERRUPTED"
        if process and process.poll() is None:
            terminate_owned(process)
    except Exception as exc:
        functional_reason = f"WORKER_RUNTIME_ERROR:{type(exc).__name__}"
        if process and process.poll() is None:
            try:
                terminate_owned(process)
            except Exception:
                pass
    finally:
        if monitor:
            monitor.stop()
            write_json(output_dir / "resource-samples.json", monitor.samples)
        if process:
            cleanup_ok = process.poll() is not None

    if monitor and monitor.violation:
        functional_reason = monitor.violation

    if worker_returncode not in {0, None} and functional_reason == "UNCLASSIFIED":
        functional_reason = f"WORKER_EXIT_{worker_returncode}"

    if not worker_json.is_file():
        final_status = "RESOURCE_BLOCKED" if monitor and monitor.violation else "FUNCTIONAL_FAIL"
        result = {
            "schema_version": "0.1",
            "profile_id": PROFILE_ID,
            "model_id": MODEL_ID,
            "workflow_id": WORKFLOW_ID,
            "workload_class": manifest.workload_class.value,
            "deployment_mode": "ONE_SHOT_BATCH",
            "started_at": started_at,
            "finished_at": utc_now(),
            "final_status": final_status,
            "reason": functional_reason,
            "model_started": True,
            "cleanup_ok": cleanup_ok,
            "warning_observed": bool(monitor and monitor.warning_observed),
            "peak_swap_delta_gib": max((x["swap_delta_gib"] for x in (monitor.samples if monitor else [])), default=0.0),
        }
        write_json(output_dir / "result.json", result)
        print(json.dumps({"result_dir": str(output_dir), **result}, ensure_ascii=False, indent=2))
        return 4

    raw = json.loads(worker_json.read_text(encoding="utf-8"))
    clip_results: dict[str, Any] = {}
    automatic_ok = True

    for label in ("zh", "en", "noisy"):
        item = raw.get(label) or {}
        payload = item.get("payload") or {}
        segments = payload.get("segments") or []
        text = str(payload.get("text", "")).strip()
        timestamp_ok = segments_valid(segments)
        nonempty = bool(text and segments)
        automatic_ok = automatic_ok and nonempty and timestamp_ok

        transcript_path = output_dir / f"{label}.transcript.txt"
        srt_path = output_dir / f"{label}.srt"
        transcript_path.write_text(render_text(payload), encoding="utf-8")
        srt_path.write_text(render_srt(payload), encoding="utf-8")

        duration = float(media_meta[label]["duration_seconds"])
        runtime = float(item.get("runtime_seconds", 0.0))
        clip_results[label] = {
            "input_basename": media_meta[label]["basename"],
            "input_sha256": media_meta[label]["sha256"],
            "duration_seconds": round(duration, 3),
            "runtime_seconds": round(runtime, 3),
            "real_time_factor": round(runtime / duration, 4),
            "detected_language": payload.get("language"),
            "segment_count": len(segments),
            "nonempty_transcript": nonempty,
            "timestamp_structure_ok": timestamp_ok,
            "transcript_sha256": sha256_file(transcript_path),
            "srt_sha256": sha256_file(srt_path),
        }

    owner_review: dict[str, Any] = {}
    if automatic_ok and not args.non_interactive:
        print("===== TRANSCRIPT PREVIEW / OWNER REVIEW =====")
        for label in ("zh", "en", "noisy"):
            preview = (output_dir / f"{label}.transcript.txt").read_text(encoding="utf-8")[:2200]
            print()
            print(f"--- {label.upper()} ---")
            print(preview)
            print()
            meaning = yes_no(f"[{label}] Meaning usable for clip selection? [y/n]: ")
            names_numbers = yes_no(f"[{label}] Names/numbers acceptable without wholesale retranscription? [y/n]: ")
            timestamps = yes_no(f"[{label}] Timestamps usable for seeking/cutting? [y/n]: ")
            owner_review[label] = {
                "meaning_usable": meaning,
                "names_numbers_acceptable": names_numbers,
                "timestamps_usable": timestamps,
            }

    peak_swap_delta = max((x["swap_delta_gib"] for x in (monitor.samples if monitor else [])), default=0.0)
    peak_swap_used = max((x["swap_used_gib"] for x in (monitor.samples if monitor else [])), default=preflight.snapshot.swap_used_gib)
    min_reclaimable = min((x["reclaimable_gib"] for x in (monitor.samples if monitor else []) if x["reclaimable_gib"] is not None), default=preflight.snapshot.reclaimable_gib)

    if monitor and monitor.violation:
        final_status = "RESOURCE_BLOCKED"
        reason = monitor.violation
    elif not cleanup_ok:
        final_status = "FUNCTIONAL_FAIL"
        reason = "CLEANUP_NOT_CONFIRMED"
    elif not automatic_ok:
        final_status = "FUNCTIONAL_FAIL"
        reason = "TRANSCRIPT_OR_TIMESTAMP_STRUCTURE_INVALID"
    elif args.non_interactive:
        final_status = "NOT_TESTED"
        reason = "OWNER_QUALITY_REVIEW_PENDING"
    elif all(
        review["meaning_usable"]
        and review["names_numbers_acceptable"]
        and review["timestamps_usable"]
        for review in owner_review.values()
    ):
        final_status = "WORKFLOW_PASS"
        reason = "REPRESENTATIVE_DIRECT_WORK_PASS"
    else:
        final_status = "QUALITY_FAIL"
        reason = "OWNER_QUALITY_GATE_FAILED"

    result = {
        "schema_version": "0.1",
        "profile_id": PROFILE_ID,
        "model_id": MODEL_ID,
        "model_path": str(MODEL_PATH),
        "workflow_id": WORKFLOW_ID,
        "workload_class": manifest.workload_class.value,
        "deployment_mode": "ONE_SHOT_BATCH",
        "started_at": started_at,
        "finished_at": utc_now(),
        "final_status": final_status,
        "reason": reason,
        "model_started": True,
        "cleanup_ok": cleanup_ok,
        "warning_observed": bool(monitor and monitor.warning_observed),
        "baseline_swap_gib": preflight.snapshot.swap_used_gib,
        "peak_swap_delta_gib": round(peak_swap_delta, 4),
        "peak_swap_used_gib": round(peak_swap_used, 4),
        "min_reclaimable_gib": None if min_reclaimable is None else round(float(min_reclaimable), 4),
        "clip_results": clip_results,
        "owner_review": owner_review,
        "private_evidence_dir": str(output_dir),
        "private_media_committed": False,
        "raw_transcripts_committed": False,
    }
    write_json(output_dir / "result.json", result)

    safe_lines = [
        f"# Whisper LIVESTREAM_STT direct-work evidence {run_id}",
        "",
        f"- Profile: `{PROFILE_ID}`",
        f"- Model: `{MODEL_ID}`",
        f"- Workflow: `{WORKFLOW_ID}`",
        f"- Workload class: `{manifest.workload_class.value}`",
        "- Deployment mode: `ONE_SHOT_BATCH`",
        f"- Final status: `{final_status}`",
        f"- Reason: `{reason}`",
        f"- Cleanup: `{cleanup_ok}`",
        f"- Warning observed: `{bool(monitor and monitor.warning_observed)}`",
        f"- Baseline swap: `{preflight.snapshot.swap_used_gib:.3f} GiB`",
        f"- Peak swap delta: `{peak_swap_delta:.3f} GiB`",
        f"- Peak swap used: `{peak_swap_used:.3f} GiB`",
        f"- Minimum reclaimable: `{min_reclaimable if min_reclaimable is not None else 'unknown'} GiB`",
        "",
        "## Clip metrics",
        "",
        "| Class | Input SHA256 | Duration s | Runtime s | RTF | Language | Segments | Timestamp structure |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for label in ("zh", "en", "noisy"):
        row = clip_results[label]
        safe_lines.append(
            f"| `{label}` | `{row['input_sha256']}` | {row['duration_seconds']} | {row['runtime_seconds']} | {row['real_time_factor']} | `{row['detected_language']}` | {row['segment_count']} | `{row['timestamp_structure_ok']}` |"
        )
    safe_lines.extend(["", "## Owner quality review", ""])
    if owner_review:
        for label in ("zh", "en", "noisy"):
            review = owner_review[label]
            safe_lines.append(
                f"- `{label}` meaning={review['meaning_usable']}, names_numbers={review['names_numbers_acceptable']}, timestamps={review['timestamps_usable']}"
            )
    else:
        safe_lines.append("- Pending manual Owner review.")
    safe_lines.extend(
        [
            "",
            "Private media and raw transcripts remain outside Git. Only this safe summary, hashes and metrics are intended for synchronization.",
            "",
        ]
    )
    safe_summary = "\n".join(safe_lines)
    (output_dir / "git-summary.md").write_text(safe_summary, encoding="utf-8")

    print()
    print("===== FINAL RESULT =====")
    print(json.dumps({
        "result_dir": str(output_dir),
        "git_summary": str(output_dir / "git-summary.md"),
        "final_status": final_status,
        "reason": reason,
        "cleanup_ok": cleanup_ok,
        "peak_swap_delta_gib": round(peak_swap_delta, 4),
        "clip_results": clip_results,
    }, ensure_ascii=False, indent=2))
    print()
    print("===== SAFE GIT SUMMARY =====")
    print(safe_summary)

    helper_path.unlink(missing_ok=True)
    return 0 if final_status == "WORKFLOW_PASS" else 5


if __name__ == "__main__":
    raise SystemExit(main())
