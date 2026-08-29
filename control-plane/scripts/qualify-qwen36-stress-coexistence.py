#!/usr/bin/env python3
"""Qwen3.6 stress-coexistence qualification under real user workloads.

This harness never starts, stops, suspends, or modifies user applications.
It only observes them. It owns one isolated oMLX process group on port 8013
and may terminate only that exact process group when a hard resource gate trips.

Two scenarios are supported:

STRESS_COLD_START
    Browser + target workload (UNITY_EDITOR or IDE) must already be running.
    Qwen3.6 is then cold-started into that workload and must complete two
    bounded functional requests plus a sustained coexistence window.

PRELOADED_COEXISTENCE
    Browser is present while Qwen3.6 is loaded first. The harness then waits
    for the user to open the target workload themselves. Once observed, the
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
MIN_SUSTAIN_SECONDS = 60.0

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

HARD_RESOURCE_REASONS = {
    "MEMORY_PRESSURE_CRITICAL",
    "RELATIVE_SWAP_GROWTH_LIMIT",
    "ABSOLUTE_SWAP_LIMIT",
    "BROWSER_WORKLOAD_LOST",
    "STRESS_TARGET_LOST",
    "RESOURCE_OBSERVATION_FAILED",
}


class Scenario(StrEnum):
    STRESS_COLD_START = "STRESS_COLD_START"
    PRELOADED_COEXISTENCE = "PRELOADED_COEXISTENCE"


class InitialWorkloadRejected(RuntimeError):
    def __init__(self, verdict: str, reason: str, message: str):
        super().__init__(message)
        self.verdict = verdict
        self.reason = reason


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
    # Use the executable column only.  Inspecting the full argv would let an
    # unrelated process satisfy a workload gate merely by mentioning an app
    # path in one of its arguments (for example, a shell command or test).
    return subprocess.check_output(
        ["ps", "ax", "-o", "pid=,rss=,comm="],
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
        raise InitialWorkloadRejected(
            "FAIL_RUNTIME",
            "DELIBERATE_WORKLOAD_REDUCTION",
            "stress qualification cannot contain deliberate workload reductions",
        )
    if manifest.workload_class is not WorkloadClass.STRESS_COEXISTENCE:
        raise InitialWorkloadRejected(
            "FAIL_RUNTIME",
            "INVALID_WORKLOAD_CLASS",
            "stress qualification requires STRESS_COEXISTENCE",
        )
    if not browser_present(raw):
        raise InitialWorkloadRejected(
            "NO_STRESS_EVIDENCE",
            "BROWSER_MISSING_AT_INITIAL_VALIDATION",
            "browser missing; stress evidence must preserve normal browser workload",
        )

    present = target_present(target, raw)
    if scenario is Scenario.STRESS_COLD_START and not present:
        raise InitialWorkloadRejected(
            "NO_STRESS_EVIDENCE",
            "STRESS_TARGET_MISSING_AT_INITIAL_VALIDATION",
            f"{target} must already be running for STRESS_COLD_START",
        )
    if scenario is Scenario.PRELOADED_COEXISTENCE and present:
        raise InitialWorkloadRejected(
            "NO_STRESS_EVIDENCE",
            "TARGET_PRESENT_AT_INITIAL_VALIDATION",
            f"{target} is already running; use STRESS_COLD_START or wait for a natural preloaded scenario"
        )

    occupied = {port: pids for port, pids in manifest.fixed_port_listeners if pids}
    if occupied:
        raise InitialWorkloadRejected(
            "FAIL_RUNTIME",
            "FIXED_PORT_OCCUPIED",
            f"fixed port already occupied: {occupied}",
        )


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
        self.target_seen_before_workload_window = False
        self._phase = "MODEL_LOAD"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._sample_lock = threading.Lock()

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

    def stop(self) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(2.0, self.interval * 3))
            if self._thread.is_alive():
                should_terminate = self.violation is None
                if should_terminate:
                    self.violation = "RESOURCE_OBSERVATION_FAILED"
                if should_terminate and self.process.poll() is None:
                    try:
                        self.terminate_owned(self.process, graceful_timeout=5.0)
                    except Exception:
                        pass
                return False
        return True

    def _record(self) -> None:
        with self._sample_lock:
            snapshot = self.probe()
            raw = self.process_reader()
            browser = browser_present(raw)
            target_now = target_present(self.target, raw)
            elapsed = time.monotonic() - self._started
            delta = snapshot.swap_used_gib - self.baseline.swap_used_gib

            if target_now and self.target_entry_elapsed_seconds is None:
                self.target_entry_elapsed_seconds = elapsed
            if target_now and self._phase in {"MODEL_LOAD", "FIRST_FUNCTIONAL_TASK"}:
                self.target_seen_before_workload_window = True

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
                if self.violation is None:
                    self.violation = "RESOURCE_OBSERVATION_FAILED"
                if self.process.poll() is None:
                    try:
                        self.terminate_owned(self.process, graceful_timeout=5.0)
                    except Exception:
                        pass
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


class RuntimeExitedBeforeHealth(RuntimeError):
    """The owned child exited before process-group ownership could be proved."""


def establish_owned_process_group(process: subprocess.Popen) -> int:
    """Prove the Popen child leads its new session without obscuring early exit."""
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError as error:
        returncode = process.poll()
        if returncode is None:
            try:
                returncode = process.wait(timeout=0)
            except subprocess.TimeoutExpired as timeout_error:
                raise RuntimeError(
                    "stress qualification process-group identity unavailable while child is live"
                ) from timeout_error
        raise RuntimeExitedBeforeHealth(
            f"RUNTIME_EXITED_BEFORE_HEALTH:{returncode}"
        ) from error
    if pgid != process.pid:
        raise RuntimeError("stress qualification process is not leader of its owned process group")
    return pgid


def wait_health_with_monitor(
    process: subprocess.Popen,
    monitor: StressResourceMonitor,
    port: int,
    timeout: float,
) -> str | None:
    """Return None on health, otherwise a structured fail-closed reason."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if monitor.violation:
            return monitor.violation
        returncode = process.poll()
        if returncode is not None:
            return monitor.violation or f"RUNTIME_EXITED_BEFORE_HEALTH:{returncode}"
        try:
            _json_get(url, 2.0)
            return None
        except Exception as error:
            last_error = error
            time.sleep(0.5)
    if monitor.violation:
        return monitor.violation
    detail = type(last_error).__name__ if last_error is not None else "UNKNOWN"
    return f"HEALTH_TIMEOUT:{detail}"


def wait_for_target_entry(
    process: subprocess.Popen,
    monitor: StressResourceMonitor,
    target: str,
    timeout: float,
) -> str | None:
    """Return None when target enters, otherwise an explicit failure reason."""
    if monitor.violation:
        return monitor.violation
    returncode = process.poll()
    if returncode is not None:
        return monitor.violation or f"RUNTIME_EXITED_WAITING_FOR_TARGET:{returncode}"
    try:
        if target_present(target):
            return "TARGET_PRESENT_BEFORE_WORKLOAD_WINDOW"
    except Exception as error:
        return f"TARGET_OBSERVATION_FAILED:{type(error).__name__}"

    print(f"WAITING_FOR_USER_WORKLOAD={target}", flush=True)
    print(
        f"Open/use {target} normally now. The harness will observe it but will not start or control it.",
        flush=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if monitor.violation:
            return monitor.violation
        returncode = process.poll()
        if returncode is not None:
            return monitor.violation or f"RUNTIME_EXITED_WAITING_FOR_TARGET:{returncode}"
        try:
            present = target_present(target)
        except Exception as error:
            return f"TARGET_OBSERVATION_FAILED:{type(error).__name__}"
        if present:
            print(f"USER_WORKLOAD_DETECTED={target}", flush=True)
            monitor.set_require_target(True)
            monitor.set_phase("STRESS_COEXISTENCE")
            return None
        time.sleep(0.5)
    return monitor.violation or "TARGET_WORKLOAD_NOT_OBSERVED"


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

    if args.qualification_port != QUALIFICATION_PORT:
        raise SystemExit(f"stress qualification must use isolated port {QUALIFICATION_PORT}")
    if args.sustain_seconds < MIN_SUSTAIN_SECONDS:
        raise SystemExit(
            f"stress qualification requires at least {MIN_SUSTAIN_SECONDS:g} sustain seconds"
        )
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
    baseline_snapshot: MemorySnapshot | None = None

    initial_failure: tuple[str, str] | None = None
    try:
        raw = process_text()
        probe = WorkloadManifestProbe(
            ports=(*PRODUCTION_PORTS, args.qualification_port),
            top_n=50,
        )
        manifest = probe.capture(WorkloadClass.STRESS_COEXISTENCE)
        write_json(manifest_path, manifest.to_dict())
        validate_initial_workload(scenario, target, manifest, raw)
    except InitialWorkloadRejected as error:
        initial_failure = (error.verdict, error.reason)
    except Exception as error:
        initial_failure = (
            "FAIL_RUNTIME",
            f"INITIAL_OBSERVATION_FAILED:{type(error).__name__}",
        )

    preflight_result = None
    if initial_failure is None:
        try:
            preflight_result = MemoryPreflight().check(QWEN36.expected_memory_gib or 0)
            preflight_reason = preflight_result.reason
            baseline_snapshot = preflight_result.snapshot
        except Exception as error:
            initial_failure = (
                "FAIL_RUNTIME",
                f"RESOURCE_PREFLIGHT_FAILED:{type(error).__name__}",
            )

    if initial_failure is not None:
        verdict, reason = initial_failure
    elif preflight_result is not None and not preflight_result.allowed:
        verdict = "STRESS_BLOCKED"
        reason = f"RESOURCE_PREFLIGHT_DENIED:{preflight_result.reason}"
    elif not ports_clear((*PRODUCTION_PORTS, args.qualification_port)):
        verdict = "FAIL_RUNTIME"
        reason = "FIXED_PORT_CHANGED_AFTER_PREFLIGHT"
    else:
        environment = os.environ.copy()
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"

        # Revalidate the user workload immediately before the load admission.
        # PRELOADED evidence is invalid if the target appeared before model load.
        fresh_raw = process_text()
        if not browser_present(fresh_raw):
            verdict = "NO_STRESS_EVIDENCE"
            reason = "BROWSER_MISSING_BEFORE_MODEL_LOAD"
        elif scenario is Scenario.STRESS_COLD_START and not target_present(target, fresh_raw):
            verdict = "NO_STRESS_EVIDENCE"
            reason = "STRESS_TARGET_MISSING_BEFORE_MODEL_LOAD"
        elif scenario is Scenario.PRELOADED_COEXISTENCE and target_present(target, fresh_raw):
            verdict = "NO_STRESS_EVIDENCE"
            reason = "TARGET_PRESENT_BEFORE_MODEL_LOAD"
        else:
            # Take a fresh safety admission immediately before Popen. This
            # snapshot is the exact baseline for load-time swap growth.
            load_preflight_result = MemoryPreflight().check(QWEN36.expected_memory_gib or 0)
            preflight_reason = load_preflight_result.reason
            baseline_snapshot = load_preflight_result.snapshot
            if not load_preflight_result.allowed:
                verdict = "STRESS_BLOCKED"
                reason = f"RESOURCE_PREFLIGHT_DENIED_AT_LOAD:{load_preflight_result.reason}"
            else:
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
                        process_group_id = establish_owned_process_group(process)

                        monitor = StressResourceMonitor(
                            process,
                            load_preflight_result.snapshot,
                            target,
                            require_target=(scenario is Scenario.STRESS_COLD_START),
                            interval=args.sample_interval,
                        )
                        monitor.start()

                        load_failure = wait_health_with_monitor(
                            process,
                            monitor,
                            args.qualification_port,
                            args.health_timeout,
                        )
                        if load_failure:
                            reason = load_failure
                        else:
                            monitor.set_phase("FIRST_FUNCTIONAL_TASK")
                            try:
                                first_functional, first_complete, _ = request_marker(
                                    args.qualification_port,
                                    "QWEN36_STRESS_FIRST_OK",
                                    args.request_timeout,
                                )
                            except (
                                urllib.error.URLError,
                                TimeoutError,
                                ConnectionError,
                                OSError,
                            ) as error:
                                reason = monitor.violation or (
                                    f"FIRST_REQUEST_FAILED:{type(error).__name__}"
                                )
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
                                        if monitor.target_seen_before_workload_window:
                                            entry_failure = (
                                                "TARGET_PRESENT_BEFORE_WORKLOAD_WINDOW"
                                            )
                                        else:
                                            entry_failure = wait_for_target_entry(
                                                process,
                                                monitor,
                                                target,
                                                args.target_wait_seconds,
                                            )
                                        if entry_failure:
                                            reason = entry_failure
                                            entered = False
                                        else:
                                            entered = True
                                    else:
                                        monitor.set_phase("STRESS_COEXISTENCE")
                                        entered = True

                                    if entered and reason == "UNCLASSIFIED":
                                        deadline = time.monotonic() + max(
                                            0.0,
                                            args.sustain_seconds,
                                        )
                                        while (
                                            time.monotonic() < deadline
                                            and monitor.violation is None
                                        ):
                                            returncode = process.poll()
                                            if returncode is not None:
                                                reason = (
                                                    "RUNTIME_EXITED_DURING_STRESS:"
                                                    f"{returncode}"
                                                )
                                                break
                                            time.sleep(
                                                min(
                                                    0.5,
                                                    max(
                                                        0.01,
                                                        deadline - time.monotonic(),
                                                    ),
                                                )
                                            )

                                        if monitor.violation:
                                            reason = monitor.violation
                                        elif reason == "UNCLASSIFIED":
                                            monitor.set_phase("SECOND_FUNCTIONAL_TASK")
                                            try:
                                                second_functional, second_complete, _ = (
                                                    request_marker(
                                                        args.qualification_port,
                                                        "QWEN36_STRESS_SECOND_OK",
                                                        args.request_timeout,
                                                    )
                                                )
                                            except (
                                                urllib.error.URLError,
                                                TimeoutError,
                                                ConnectionError,
                                                OSError,
                                            ) as error:
                                                reason = monitor.violation or (
                                                    "SECOND_REQUEST_FAILED:"
                                                    f"{type(error).__name__}"
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
                            monitor.set_phase("FINAL_RESOURCE_CHECK")
                            try:
                                monitor._record()
                            except Exception:
                                monitor.violation = "RESOURCE_OBSERVATION_FAILED"
                            monitor_stopped = monitor.stop()
                            if not monitor_stopped or monitor.violation:
                                reason = monitor.violation or "RESOURCE_OBSERVATION_FAILED"
                            with samples_path.open("w", encoding="utf-8") as handle:
                                for sample in monitor.samples:
                                    handle.write(
                                        json.dumps(asdict(sample), sort_keys=True) + "\n"
                                    )

                        if (
                            reason == "STRESS_QUALIFICATION_COMPLETE"
                            and first_functional
                            and first_complete
                            and second_functional
                            and second_complete
                        ):
                            stress_samples = [
                                item
                                for item in (monitor.samples if monitor else [])
                                if item.phase
                                in {"STRESS_COEXISTENCE", "SECOND_FUNCTIONAL_TASK"}
                            ]
                            if not stress_samples:
                                reason = "RESOURCE_OBSERVATION_FAILED"
                                verdict = "STRESS_BLOCKED"
                            elif not all(item.target_present for item in stress_samples):
                                reason = "STRESS_TARGET_LOST"
                                verdict = "STRESS_BLOCKED"
                            else:
                                verdict = (
                                    "PASS_WITH_WARNING"
                                    if monitor and monitor.warning_observed
                                    else "PASS"
                                )
                        elif reason in HARD_RESOURCE_REASONS:
                            verdict = "STRESS_BLOCKED"
                        elif reason in {
                            "TARGET_WORKLOAD_NOT_OBSERVED",
                            "TARGET_PRESENT_BEFORE_MODEL_LOAD",
                            "TARGET_PRESENT_BEFORE_WORKLOAD_WINDOW",
                            "STRESS_TARGET_MISSING_BEFORE_MODEL_LOAD",
                            "BROWSER_MISSING_BEFORE_MODEL_LOAD",
                        }:
                            verdict = "NO_STRESS_EVIDENCE"
                        else:
                            verdict = "FAIL_RUNTIME"
                except RuntimeExitedBeforeHealth as error:
                    reason = str(error)
                    verdict = "FAIL_RUNTIME"
                except Exception as error:
                    if reason == "UNCLASSIFIED":
                        reason = f"RUNTIME_SETUP_FAILED:{type(error).__name__}"
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

    try:
        qualification_port_clear = ports_clear((args.qualification_port,))
        production_ports_clear = ports_clear(PRODUCTION_PORTS)
    except Exception:
        qualification_port_clear = False
        production_ports_clear = False
        if verdict in {"PASS", "PASS_WITH_WARNING"}:
            verdict = "FAIL_RUNTIME"
            reason = "FINAL_PORT_OBSERVATION_FAILED"
    else:
        if process is None:
            cleanup_ok = qualification_port_clear
        if not production_ports_clear:
            cleanup_ok = False
            if verdict in {"PASS", "PASS_WITH_WARNING"}:
                verdict = "FAIL_RUNTIME"
                reason = "PRODUCTION_OR_OTHER_QUALIFICATION_PORT_MUTATION"

    samples = monitor.samples if monitor else []
    peak_delta = max((item.swap_delta_gib for item in samples), default=0.0)
    baseline_swap = baseline_snapshot.swap_used_gib if baseline_snapshot else 0.0
    peak_swap = max([baseline_swap, *[item.swap_used_gib for item in samples]])
    reclaimable = [
        item.reclaimable_gib
        for item in samples
        if item.reclaimable_gib is not None
    ]
    min_reclaimable = min(reclaimable) if reclaimable else None
    if samples:
        browser_throughout = all(item.browser_present for item in samples)
    else:
        try:
            browser_throughout = browser_present()
        except Exception:
            browser_throughout = False
    stress_samples = [
        item
        for item in samples
        if item.phase in {"STRESS_COEXISTENCE", "SECOND_FUNCTIONAL_TASK"}
    ]
    target_during_stress = bool(stress_samples) and all(
        item.target_present for item in stress_samples
    )

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
        min_reclaimable_gib=(
            round(min_reclaimable, 4) if min_reclaimable is not None else None
        ),
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
    print(
        json.dumps(
            {
                "result_dir": str(output_dir),
                "verdict": verdict,
                "reason": reason,
                "scenario": scenario.value,
                "target": target,
            }
        )
    )
    return 0 if verdict in {"PASS", "PASS_WITH_WARNING"} else 1


if __name__ == "__main__":
    sys.exit(main())
