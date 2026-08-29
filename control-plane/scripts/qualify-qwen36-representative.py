#!/usr/bin/env python3
"""Representative-workload qualification for the existing Qwen3.6 fallback.

The harness is intentionally isolated from production routing. It starts one
exact-owned oMLX process on a qualification-only loopback port, observes the
user workload without controlling it, runs one bounded FAST/chat task, keeps the
model resident for a short coexistence window, and then terminates only the
process group it created.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from local_ai_control.services.heavy_process_identity import listener_pids
from local_ai_control.services.models import MemoryPreflight, MemorySnapshot, QWEN36
from local_ai_control.services.workload_admission import WorkloadClass, WorkloadManifestProbe

QUALIFICATION_PORT = 8012
PRODUCTION_PORTS = (8000, 8001, 8011)
OMLX_BIN = Path("/Users/jerson/AI/runtime/omlx-venv/bin/omlx")
MODEL_DIR = Path("/Users/jerson/AI/models")
MODEL_NAME = "Qwen3.6-35B-A3B-4bit"
SWAP_GROWTH_LIMIT_GIB = 2.0
ABSOLUTE_SWAP_LIMIT_GIB = 6.0
BROWSER_PATTERNS = (
    "/Google Chrome.app/",
    "/Safari.app/",
    "/Firefox.app/",
    "/Arc.app/",
    "/Microsoft Edge.app/",
)


@dataclass(frozen=True)
class ResourceSample:
    timestamp: str
    elapsed_seconds: float
    phase: str
    pressure: str
    reclaimable_gib: float | None
    swap_used_gib: float
    swap_delta_gib: float
    browser_present: bool


@dataclass(frozen=True)
class QualificationResult:
    verdict: str
    reason: str
    workload_class: str
    profile_id: str
    model_name: str
    qualification_port: int
    preflight_reason: str
    functional_pass: bool
    response_complete: bool
    response_text: str
    warning_observed: bool
    peak_swap_delta_gib: float
    peak_swap_used_gib: float
    browser_present_throughout: bool
    sustain_seconds: float
    process_pid: int | None
    process_group_id: int | None
    cleanup_ok: bool
    started_at: str
    finished_at: str


def runtime_command(port: int = QUALIFICATION_PORT) -> list[str]:
    return [
        str(OMLX_BIN),
        "serve",
        "--model-dir",
        str(MODEL_DIR),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--max-concurrent-requests",
        "1",
        "--memory-guard-gb",
        "28",
        "--no-cache",
        "--initial-cache-blocks",
        "64",
    ]


def _process_text() -> str:
    # Use executable identity only. Full argv can be spoofed by an unrelated
    # process whose arguments merely mention a browser application path.
    return subprocess.check_output(["ps", "ax", "-o", "pid=,rss=,comm="], text=True)


def browser_present(raw: str | None = None) -> bool:
    text = _process_text() if raw is None else raw
    return any(pattern in text for pattern in BROWSER_PATTERNS)


def validate_representative_manifest(manifest) -> None:
    if manifest.workload_class is not WorkloadClass.REPRESENTATIVE_WORKLOAD:
        raise RuntimeError("qualification requires REPRESENTATIVE_WORKLOAD")
    if manifest.deliberate_reductions:
        raise RuntimeError("representative qualification cannot contain deliberate workload reductions")
    categories = {item.category for item in manifest.material_applications}
    if "BROWSER" not in categories:
        raise RuntimeError("representative baseline lost the browser; do not manufacture headroom")
    occupied = {port: pids for port, pids in manifest.fixed_port_listeners if pids}
    if occupied:
        raise RuntimeError(f"fixed port already occupied: {occupied}")


def _json_get(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def wait_health(process: subprocess.Popen, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"oMLX exited before health gate with code {process.returncode}")
        try:
            _json_get(url, 2.0)
            return
        except Exception as error:  # health polling is intentionally tolerant
            last_error = error
            time.sleep(0.5)
    raise RuntimeError(f"oMLX health timeout: {last_error}")


def wait_health_with_monitor(
    process: subprocess.Popen,
    port: int,
    timeout: float,
    monitor: "ResourceMonitor",
) -> str | None:
    """Preserve resource-gate classification while health/model load is pending."""
    try:
        wait_health(process, port, timeout)
    except RuntimeError:
        if monitor.violation:
            return monitor.violation
        raise
    return monitor.violation


def request_fast_task(port: int, timeout: float) -> tuple[bool, bool, str]:
    marker = "QWEN36_REPRESENTATIVE_OK"
    body = json.dumps(
        {
            "model": MODEL_NAME,
            "input": (
                "This is a bounded local qualification task. "
                f"Reply with exactly this marker and nothing else: {marker}"
            ),
            "max_output_tokens": 48,
            "chat_template_kwargs": {"enable_thinking": False},
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/responses",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    incomplete = payload.get("incomplete_details") or {}
    complete = payload.get("status") == "completed" and not incomplete
    text = ""
    if isinstance(payload.get("output_text"), str):
        text = payload["output_text"]
    else:
        for item in payload.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    text += content["text"]
    functional = text.strip() == marker
    return functional, complete, text.strip()


class ResourceMonitor:
    """Read host state and stop only the exact-owned qualification process."""

    def __init__(
        self,
        process: subprocess.Popen,
        baseline: MemorySnapshot,
        *,
        interval: float = 1.0,
        probe=None,
        browser_check=None,
        terminate_owned=None,
    ):
        self.process = process
        self.baseline = baseline
        self.interval = max(0.1, float(interval))
        self.probe = probe or MemoryPreflight().probe
        self.browser_check = browser_check or browser_present
        self.terminate_owned = terminate_owned or terminate_process_group
        self.samples: list[ResourceSample] = []
        self.violation: str | None = None
        self.warning_observed = False
        self._phase = "MODEL_LOAD"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    def start(self) -> None:
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="qwen36-resource-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(2.0, self.interval * 3))

    def _record(self) -> None:
        snapshot = self.probe()
        delta = snapshot.swap_used_gib - self.baseline.swap_used_gib
        present = bool(self.browser_check())
        self.samples.append(
            ResourceSample(
                timestamp=datetime.now(UTC).isoformat(),
                elapsed_seconds=time.monotonic() - self._started,
                phase=self._phase,
                pressure=snapshot.pressure,
                reclaimable_gib=snapshot.reclaimable_gib,
                swap_used_gib=snapshot.swap_used_gib,
                swap_delta_gib=delta,
                browser_present=present,
            )
        )
        if snapshot.pressure == "WARNING":
            self.warning_observed = True
        if snapshot.pressure == "CRITICAL":
            self.violation = "MEMORY_PRESSURE_CRITICAL"
        elif delta > SWAP_GROWTH_LIMIT_GIB:
            self.violation = "RELATIVE_SWAP_GROWTH_LIMIT"
        elif snapshot.swap_used_gib > ABSOLUTE_SWAP_LIMIT_GIB:
            self.violation = "ABSOLUTE_SWAP_LIMIT"
        elif not present:
            self.violation = "REPRESENTATIVE_BROWSER_LOST"
        if self.violation and self.process.poll() is None:
            self.terminate_owned(self.process, graceful_timeout=5.0)

    def _run(self) -> None:
        while not self._stop.is_set() and self.violation is None:
            try:
                self._record()
            except Exception:
                self.violation = "RESOURCE_OBSERVATION_FAILED"
                if self.process.poll() is None:
                    self.terminate_owned(self.process, graceful_timeout=5.0)
                return
            self._stop.wait(self.interval)


def terminate_process_group(
    process: subprocess.Popen,
    *,
    graceful_timeout: float = 15.0,
    killpg=os.killpg,
) -> bool:
    """Terminate only the process group created by this harness."""
    if process.poll() is not None:
        return True
    pgid = os.getpgid(process.pid)
    if pgid != process.pid:
        raise RuntimeError("qualification process is not leader of its owned process group")
    killpg(pgid, signal.SIGTERM)
    try:
        process.wait(timeout=graceful_timeout)
        return True
    except subprocess.TimeoutExpired:
        killpg(pgid, signal.SIGKILL)
        process.wait(timeout=10)
        return True


def ports_clear(ports: tuple[int, ...]) -> bool:
    return all(not listener_pids(port) for port in ports)


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--qualification-port", type=int, default=QUALIFICATION_PORT)
    parser.add_argument("--health-timeout", type=float, default=90.0)
    parser.add_argument("--request-timeout", type=float, default=180.0)
    parser.add_argument("--sustain-seconds", type=float, default=60.0)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.qualification_port in PRODUCTION_PORTS:
        raise SystemExit("qualification port must not be a production port")
    if not OMLX_BIN.exists():
        raise SystemExit(f"missing oMLX runtime: {OMLX_BIN}")

    output_dir = args.output_dir or Path(
        f"/tmp/qwen36-representative-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    log_path = output_dir / "worker.log"
    samples_path = output_dir / "samples.jsonl"
    result_path = output_dir / "result.json"
    manifest_path = output_dir / "workload-manifest.json"

    started_at = datetime.now(UTC).isoformat()
    process: subprocess.Popen | None = None
    process_pid: int | None = None
    process_group_id: int | None = None
    monitor: ResourceMonitor | None = None
    preflight_reason = "NOT_RUN"
    functional_pass = False
    response_complete = False
    response_text = ""
    cleanup_ok = False
    verdict = "FAIL_RUNTIME"
    reason = "UNCLASSIFIED"

    probe = WorkloadManifestProbe(ports=(*PRODUCTION_PORTS, args.qualification_port), top_n=50)
    manifest = probe.capture(WorkloadClass.REPRESENTATIVE_WORKLOAD)
    write_json(manifest_path, manifest.to_dict())
    validate_representative_manifest(manifest)

    preflight = MemoryPreflight()
    preflight_result = preflight.check(QWEN36.expected_memory_gib or 0)
    preflight_reason = preflight_result.reason
    if not preflight_result.allowed:
        verdict = "REPRESENTATIVE_BLOCKED"
        reason = f"RESOURCE_PREFLIGHT_DENIED:{preflight_result.reason}"
        result = QualificationResult(
            verdict, reason, manifest.workload_class.value, QWEN36.profile_id, MODEL_NAME,
            args.qualification_port, preflight_reason, False, False, "", False, 0.0,
            preflight_result.snapshot.swap_used_gib, True, 0.0, None, None, True,
            started_at, datetime.now(UTC).isoformat(),
        )
        write_json(result_path, asdict(result))
        print(json.dumps({"result_dir": str(output_dir), "verdict": verdict, "reason": reason}))
        return 2

    if not ports_clear((*PRODUCTION_PORTS, args.qualification_port)):
        raise RuntimeError("fixed ports changed after manifest capture")

    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"

    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                runtime_command(args.qualification_port),
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd="/Users/jerson/AI",
                env=environment,
                start_new_session=True,
            )
            process_pid = process.pid
            process_group_id = os.getpgid(process.pid)
            if process_group_id != process_pid:
                raise RuntimeError("qualification process group ownership proof failed")

            # MODEL_LOAD monitoring begins immediately after exact-owned process
            # identity is proven. The baseline is the representative preflight
            # snapshot, so load-time swap growth cannot disappear behind /health.
            monitor = ResourceMonitor(
                process,
                preflight_result.snapshot,
                interval=args.sample_interval,
            )
            monitor.start()

            load_violation = wait_health_with_monitor(
                process,
                args.qualification_port,
                args.health_timeout,
                monitor,
            )
            if load_violation:
                reason = load_violation
            elif not browser_present():
                reason = "REPRESENTATIVE_BROWSER_LOST"
            else:
                monitor.set_phase("FUNCTIONAL_TASK")
                try:
                    functional_pass, response_complete, response_text = request_fast_task(
                        args.qualification_port,
                        args.request_timeout,
                    )
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                    if monitor.violation:
                        reason = monitor.violation
                    else:
                        reason = f"MODEL_REQUEST_FAILED:{type(error).__name__}"
                else:
                    if monitor.violation:
                        reason = monitor.violation
                    elif not response_complete:
                        reason = "INCOMPLETE_RESPONSE"
                    elif not functional_pass:
                        reason = "FUNCTIONAL_MISMATCH"
                    else:
                        monitor.set_phase("SUSTAINED_COEXISTENCE")
                        deadline = time.monotonic() + max(0.0, args.sustain_seconds)
                        while time.monotonic() < deadline and monitor.violation is None:
                            if process.poll() is not None:
                                reason = "RUNTIME_EXITED_DURING_COEXISTENCE"
                                break
                            time.sleep(min(0.5, max(0.01, deadline - time.monotonic())))
                        else:
                            if monitor.violation:
                                reason = monitor.violation
                            elif reason == "UNCLASSIFIED":
                                reason = "QUALIFICATION_COMPLETE"

            if monitor:
                monitor.stop()
                with samples_path.open("w", encoding="utf-8") as handle:
                    for sample in monitor.samples:
                        handle.write(json.dumps(asdict(sample), sort_keys=True) + "\n")

            if reason == "QUALIFICATION_COMPLETE" and functional_pass and response_complete:
                verdict = "PASS_WITH_WARNING" if monitor and monitor.warning_observed else "PASS"
            elif reason in {
                "MEMORY_PRESSURE_CRITICAL",
                "RELATIVE_SWAP_GROWTH_LIMIT",
                "ABSOLUTE_SWAP_LIMIT",
                "REPRESENTATIVE_BROWSER_LOST",
                "RESOURCE_OBSERVATION_FAILED",
            }:
                verdict = "REPRESENTATIVE_BLOCKED"
            else:
                verdict = "FAIL_RUNTIME"
    finally:
        if monitor:
            monitor.stop()
        if process:
            try:
                terminate_process_group(process)
            except Exception:
                cleanup_ok = False
            else:
                time.sleep(1.0)
                cleanup_ok = ports_clear((args.qualification_port,))
        else:
            cleanup_ok = True

    if not ports_clear(PRODUCTION_PORTS):
        cleanup_ok = False
        if verdict in {"PASS", "PASS_WITH_WARNING"}:
            verdict = "FAIL_RUNTIME"
            reason = "PRODUCTION_PORT_MUTATION"

    samples = monitor.samples if monitor else []
    peak_delta = max((item.swap_delta_gib for item in samples), default=0.0)
    peak_swap = max(
        [preflight_result.snapshot.swap_used_gib, *[item.swap_used_gib for item in samples]]
    )
    browser_throughout = all(item.browser_present for item in samples) if samples else browser_present()
    if not cleanup_ok and verdict in {"PASS", "PASS_WITH_WARNING"}:
        verdict = "FAIL_RUNTIME"
        reason = "CLEANUP_FAILED"

    result = QualificationResult(
        verdict=verdict,
        reason=reason,
        workload_class=manifest.workload_class.value,
        profile_id=QWEN36.profile_id,
        model_name=MODEL_NAME,
        qualification_port=args.qualification_port,
        preflight_reason=preflight_reason,
        functional_pass=functional_pass,
        response_complete=response_complete,
        response_text=response_text,
        warning_observed=bool(monitor and monitor.warning_observed),
        peak_swap_delta_gib=round(peak_delta, 4),
        peak_swap_used_gib=round(peak_swap, 4),
        browser_present_throughout=browser_throughout,
        sustain_seconds=args.sustain_seconds if reason == "QUALIFICATION_COMPLETE" else 0.0,
        process_pid=process_pid,
        process_group_id=process_group_id,
        cleanup_ok=cleanup_ok,
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(),
    )
    write_json(result_path, asdict(result))
    print(json.dumps({"result_dir": str(output_dir), "verdict": verdict, "reason": reason}))
    return 0 if verdict in {"PASS", "PASS_WITH_WARNING"} else 1


if __name__ == "__main__":
    sys.exit(main())