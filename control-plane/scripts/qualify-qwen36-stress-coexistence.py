#!/usr/bin/env python3
"""Qwen3.6 stress-coexistence qualification under real user workloads.

This harness never starts, stops, suspends, or modifies user applications.
It only observes them.  It owns one isolated oMLX process group on port 8013
and may terminate only that exact process group when a hard resource gate trips.

Two scenarios are supported:

STRESS_COLD_START
    Browser + target workload (UNITY_EDITOR or IDE) must already be running.
    Qwen3.6 is then cold-started into that workload and must complete two
    bounded functional requests plus a sustained coexistence window.

PRELOADED_COEXISTENCE
    Browser is present while Qwen3.6 is loaded first.  The harness then waits
    for the user to open the target workload themselves.  Once observed, the
    model must remain healthy and functional through sustained coexistence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
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

QUALIFICATION_PORT = 8013
PRODUCTION_PORTS = (8000, 8001, 8011, 8012)
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

TARGET_PATTERNS = {
    "UNITY_EDITOR": (
        "/Unity.app/Contents/MacOS/Unity",
    ),
    "IDE": (
        "/Visual Studio Code.app/",
        "/Cursor.app/",
        "/Xcode.app/",
        "/IntelliJ IDEA.app/",
        "/PyCharm.app/",
    ),
}


class Scenario(StrEnum):
    STRESS_COLD_START = "STRESS_COLD_START"
    PRELOADED_COEXISTENCE = "PRELOADED_COEXISTENCE"


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
    target_present: bool


@dataclass(frozen=True)
class StressResult:
    verdict: str
    reason: str
    scenario: str
    target: str
    workload_class: str
    profile_id: str
    model_name: str
    qualification_port: int
    preflight_reason: str
    first_functional_pass: bool
    second_functional_pass: bool
    first_response_complete: bool
    second_response_complete: bool
    warning_observed: bool
    peak_swap_delta_gib: float
    peak_swap_used_gib: float
    min_reclaimable_gib: float | None
    browser_present_throughout: bool
    target_present_during_stress: bool
    target_entry_elapsed_seconds: float | None
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


def process_text() -> str:
    return subprocess.check_output(
        ["ps", "ax", "-o", "pid=,rss=,command="],
        text=True,
    )


def any_pattern_present(raw: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in raw for pattern in patterns)


def browser_present(raw: str | None = None) -> bool:
    return any_pattern_present(process_text() if raw is None else raw, BROWSER_PATTERNS)


def target_present(target: str, raw: str | None = None) -> bool:
    if target not in TARGET_PATTERNS:
        raise ValueError(f"unsupported target: {target}")
    return any_pattern_present(
        process_text() if raw is None else raw,
        TARGET_PATTERNS[target],
    )


def validate_initial_workload(scenario: Scenario, target: str, manifest, raw: str) -> None:
    if manifest.deliberate_reductions:
        raise RuntimeError("stress qualification cannot contain deliberate workload reductions")
    if manifest.workload_class is not WorkloadClass.STRESS_COEXISTENCE:
        raise RuntimeError("stress qualification requires STRESS_COEXISTENCE")
    if not browser_present(raw):
        raise RuntimeError("browser missing; stress evidence must preserve normal browser workload")

    present = target_present(target, raw)
    if scenario is Scenario.STRESS_COLD_START and not present:
        raise RuntimeError(f"{target} must already be running for STRESS_COLD_START")
    if scenario is Scenario.PRELOADED_COEXISTENCE and present:
        raise RuntimeError(
            f"{target} is already running; use STRESS_COLD_START or wait for a natural preloaded scenario"
        )

    occupied = {port: pids for port, pids in manifest.fixed_port_listeners if pids}
    if occupied:
        raise RuntimeError(f"fixed port already occupied: {occupied}")


def _json_get(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def request_marker(port: int, marker: str, timeout: float) -> tuple[bool, bool, str]:
    body = json.dumps(
        {
            "model": MODEL_NAME,
            "input": (
                "This is a bounded local stress qualification task. "
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
    return text.strip() == marker, complete, text.strip()


class StressResourceMonitor:
    """Observe the host and abort only the exact-owned qualification runtime."""

    def __init__(
        self,
        process: subprocess.Popen,
        baseline: MemorySnapshot,
        target: str,
        *,
        require_target: bool,
        interval: float = 1.0,
        probe=None,
        process_reader=None,
        terminate_owned=None,
    ):
        self.process = process
        self.baseline = baseline
        self.target = target
        self.require_target = bool(require_target)
        self.interval = max(0.1, float(interval))
        self.probe = probe or MemoryPreflight().probe
        self.process_reader = process_reader or process_text
        self.terminate_owned = terminate_owned or terminate_process_group
        self.samples: list[ResourceSample] = []
        self.violation: str | None = None
        self.warning_observed = False
        self.target_entry_elapsed_seconds: float | None = None
        self._phase = "MODEL_LOAD"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    def set_require_target(self, required: bool) -> None:
        self.require_target = bool(required)

    def start(self) -> None:
        self._started = time.monotonic()
        self._thread = threading.Thread(
            target=self._run,
            name="qwen36-stress-resource-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(2.0, self.interval * 3))

    def _record(self) -> None:
        snapshot = self.probe()
        raw = self.process_reader()
        browser = browser_present(raw)
        target_now = target_present(self.target, raw)
        elapsed = time.monotonic() - self._started
        delta = snapshot.swap_used_gib - self.baseline.swap_used_gib

        if target_now and self.target_entry_elapsed_seconds is None:
            self.target_entry_elapsed_seconds = elapsed

        self.samples.append(
            ResourceSample(
                timestamp=datetime.now(UTC).isoformat(),
                elapsed_seconds=elapsed,
                phase=self._phase,
                pressure=snapshot.pressure,
                reclaimable_gib=snapshot.reclaimable_gib,
                swap_used_gib=snapshot.swap_used_gib,
                swap_delta_gib=delta,
                browser_present=browser,
                target_present=target_now,
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
        elif not browser:
            self.violation = "BROWSER_WORKLOAD_LOST"
        elif self.require_target and not target_now:
            self.violation = "STRESS_TARGET_LOST"

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
    if process.poll() is not None:
        return True
    pgid = os.getpgid(process.pid)
    if pgid != process.pid:
        raise RuntimeError("stress qualification process is not leader of its owned process group")
    killpg(pgid, signal.SIGTERM)
    try:
        process.wait(timeout=graceful_timeout)
        return True
    except subprocess.TimeoutExpired:
        killpg(pgid, signal.SIGKILL)
        process.wait(timeout=10)
        return True


def wait_health_with_monitor(
    process: subprocess.Popen,
    monitor: StressResourceMonitor,
    port: int,
    timeout: float,
) -> str | None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if monitor.violation:
            return monitor.violation
        if process.poll() is not None:
            if monitor.violation:
                return monitor.violation
            raise RuntimeError(f"oMLX exited before health gate with code {process.returncode}")
        try:
            _json_get(url, 2.0)
            return None
        except Exception as error:
            last_error = error
            time.sleep(0.5)
    if monitor.violation:
        return monitor.violation
    raise RuntimeError(f"oMLX health timeout: {last_error}")


def wait_for_target_entry(
    process: subprocess.Popen,
    monitor: StressResourceMonitor,
    target: str,
    timeout: float,
) -> bool:
    print(f"WAITING_FOR_USER_WORKLOAD={target}", flush=True)
    print(
        f"Open/use {target} normally now. The harness will observe it but will not start or control it.",
        flush=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if monitor.violation:
            return False
        if process.poll() is not None:
            return False
        if target_present(target):
            print(f"USER_WORKLOAD_DETECTED={target}", flush=True)
            monitor.set_require_target(True)
            monitor.set_phase("STRESS_COEXISTENCE")
            return True
        time.sleep(0.5)
    return False


def ports_clear(ports: tuple[int, ...]) -> bool:
    return all(not listener_pids(port) for port in ports)


def write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=[item.value for item in Scenario], required=True)
    parser.add_argument("--target", choices=sorted(TARGET_PATTERNS), required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--qualification-port", type=int, default=QUALIFICATION_PORT)
    parser.add_argument("--health-timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=240.0)
    parser.add_argument("--target-wait-seconds", type=float, default=300.0)
    parser.add_argument("--sustain-seconds", type=float, default=180.0)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenario = Scenario(args.scenario)
    target = args.target

    if args.qualification_port in PRODUCTION_PORTS:
        raise SystemExit("stress qualification port must not be a production/other qualification port")
    if not OMLX_BIN.exists():
        raise SystemExit(f"missing oMLX runtime: {OMLX_BIN}")

    output_dir = args.output_dir or Path(
        f"/tmp/qwen36-stress-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
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
    monitor: StressResourceMonitor | None = None
    first_functional = False
    second_functional = False
    first_complete = False
    second_complete = False
    verdict = "FAIL_RUNTIME"
    reason = "UNCLASSIFIED"
    cleanup_ok = False
    preflight_reason = "NOT_RUN"

    raw = process_text()
    probe = WorkloadManifestProbe(
        ports=(*PRODUCTION_PORTS, args.qualification_port),
        top_n=50,
    )
    manifest = probe.capture(WorkloadClass.STRESS_COEXISTENCE)
    write_json(manifest_path, manifest.to_dict())
    validate_initial_workload(scenario, target, manifest, raw)

    preflight_result = MemoryPreflight().check(QWEN36.expected_memory_gib or 0)
    preflight_reason = preflight_result.reason
    if not preflight_result.allowed:
        verdict = "STRESS_BLOCKED"
        reason = f"RESOURCE_PREFLIGHT_DENIED:{preflight_result.reason}"
    elif not ports_clear((*PRODUCTION_PORTS, args.qualification_port)):
        verdict = "FAIL_RUNTIME"
        reason = "FIXED_PORT_CHANGED_AFTER_PREFLIGHT"
    else:
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
                    raise RuntimeError("stress qualification process group ownership proof failed")

                monitor = StressResourceMonitor(
                    process,
                    preflight_result.snapshot,
                    target,
                    require_target=(scenario is Scenario.STRESS_COLD_START),
                    interval=args.sample_interval,
                )
                monitor.start()

                load_violation = wait_health_with_monitor(
                    process,
                    monitor,
                    args.qualification_port,
                    args.health_timeout,
                )
                if load_violation:
                    reason = load_violation
                else:
                    monitor.set_phase("FIRST_FUNCTIONAL_TASK")
                    try:
                        first_functional, first_complete, _ = request_marker(
                            args.qualification_port,
                            "QWEN36_STRESS_FIRST_OK",
                            args.request_timeout,
                        )
                    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                        reason = monitor.violation or f"FIRST_REQUEST_FAILED:{type(error).__name__}"
                    else:
                        if monitor.violation:
                            reason = monitor.violation
                        elif not first_complete:
                            reason = "FIRST_RESPONSE_INCOMPLETE"
                        elif not first_functional:
                            reason = "FIRST_FUNCTIONAL_MISMATCH"
                        else:
                            if scenario is Scenario.PRELOADED_COEXISTENCE:
                                monitor.set_phase("WAITING_FOR_USER_WORKLOAD")
                                entered = wait_for_target_entry(
                                    process,
                                    monitor,
                                    target,
                                    args.target_wait_seconds,
                                )
                                if not entered:
                                    reason = monitor.violation or "TARGET_WORKLOAD_NOT_OBSERVED"
                            else:
                                monitor.set_phase("STRESS_COEXISTENCE")
                                entered = True

                            if entered and reason == "UNCLASSIFIED":
                                deadline = time.monotonic() + max(0.0, args.sustain_seconds)
                                while time.monotonic() < deadline and monitor.violation is None:
                                    if process.poll() is not None:
                                        reason = "RUNTIME_EXITED_DURING_STRESS"
                                        break
                                    time.sleep(min(0.5, max(0.01, deadline - time.monotonic())))

                                if monitor.violation:
                                    reason = monitor.violation
                                elif reason == "UNCLASSIFIED":
                                    monitor.set_phase("SECOND_FUNCTIONAL_TASK")
                                    try:
                                        second_functional, second_complete, _ = request_marker(
                                            args.qualification_port,
                                            "QWEN36_STRESS_SECOND_OK",
                                            args.request_timeout,
                                        )
                                    except (
                                        urllib.error.URLError,
                                        TimeoutError,
                                        ConnectionError,
                                        OSError,
                                    ) as error:
                                        reason = monitor.violation or (
                                            f"SECOND_REQUEST_FAILED:{type(error).__name__}"
                                        )
                                    else:
                                        if monitor.violation:
                                            reason = monitor.violation
                                        elif not second_complete:
                                            reason = "SECOND_RESPONSE_INCOMPLETE"
                                        elif not second_functional:
                                            reason = "SECOND_FUNCTIONAL_MISMATCH"
                                        else:
                                            reason = "STRESS_QUALIFICATION_COMPLETE"

                if monitor:
                    monitor.stop()
                    with samples_path.open("w", encoding="utf-8") as handle:
                        for sample in monitor.samples:
                            handle.write(json.dumps(asdict(sample), sort_keys=True) + "\n")

                if (
                    reason == "STRESS_QUALIFICATION_COMPLETE"
                    and first_functional
                    and first_complete
                    and second_functional
                    and second_complete
                ):
                    verdict = "PASS_WITH_WARNING" if monitor and monitor.warning_observed else "PASS"
                elif reason in {
                    "MEMORY_PRESSURE_CRITICAL",
                    "RELATIVE_SWAP_GROWTH_LIMIT",
                    "ABSOLUTE_SWAP_LIMIT",
                    "BROWSER_WORKLOAD_LOST",
                    "STRESS_TARGET_LOST",
                    "RESOURCE_OBSERVATION_FAILED",
                }:
                    verdict = "STRESS_BLOCKED"
                elif reason == "TARGET_WORKLOAD_NOT_OBSERVED":
                    verdict = "NO_STRESS_EVIDENCE"
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
            reason = "PRODUCTION_OR_OTHER_QUALIFICATION_PORT_MUTATION"

    samples = monitor.samples if monitor else []
    peak_delta = max((item.swap_delta_gib for item in samples), default=0.0)
    peak_swap = max(
        [preflight_result.snapshot.swap_used_gib, *[item.swap_used_gib for item in samples]]
    ) if 'preflight_result' in locals() else 0.0
    reclaimable = [
        item.reclaimable_gib
        for item in samples
        if item.reclaimable_gib is not None
    ]
    min_reclaimable = min(reclaimable) if reclaimable else None
    browser_throughout = all(item.browser_present for item in samples) if samples else browser_present()
    stress_samples = [
        item for item in samples
        if item.phase in {"STRESS_COEXISTENCE", "SECOND_FUNCTIONAL_TASK"}
    ]
    target_during_stress = bool(stress_samples) and all(item.target_present for item in stress_samples)

    if not cleanup_ok and verdict in {"PASS", "PASS_WITH_WARNING"}:
        verdict = "FAIL_RUNTIME"
        reason = "CLEANUP_FAILED"

    result = StressResult(
        verdict=verdict,
        reason=reason,
        scenario=scenario.value,
        target=target,
        workload_class=WorkloadClass.STRESS_COEXISTENCE.value,
        profile_id=QWEN36.profile_id,
        model_name=MODEL_NAME,
        qualification_port=args.qualification_port,
        preflight_reason=preflight_reason,
        first_functional_pass=first_functional,
        second_functional_pass=second_functional,
        first_response_complete=first_complete,
        second_response_complete=second_complete,
        warning_observed=bool(monitor and monitor.warning_observed),
        peak_swap_delta_gib=round(peak_delta, 4),
        peak_swap_used_gib=round(peak_swap, 4),
        min_reclaimable_gib=(round(min_reclaimable, 4) if min_reclaimable is not None else None),
        browser_present_throughout=browser_throughout,
        target_present_during_stress=target_during_stress,
        target_entry_elapsed_seconds=(
            round(monitor.target_entry_elapsed_seconds, 3)
            if monitor and monitor.target_entry_elapsed_seconds is not None
            else None
        ),
        sustain_seconds=(
            args.sustain_seconds if reason == "STRESS_QUALIFICATION_COMPLETE" else 0.0
        ),
        process_pid=process_pid,
        process_group_id=process_group_id,
        cleanup_ok=cleanup_ok,
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(),
    )
    write_json(result_path, asdict(result))
    print(json.dumps({
        "result_dir": str(output_dir),
        "verdict": verdict,
        "reason": reason,
        "scenario": scenario.value,
        "target": target,
    }))
    return 0 if verdict in {"PASS", "PASS_WITH_WARNING"} else 1


if __name__ == "__main__":
    sys.exit(main())
