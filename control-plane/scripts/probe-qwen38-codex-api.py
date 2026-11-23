#!/usr/bin/env python3
"""Isolated Qwen3.8 local API admission/smoke. Does not configure Codex.

Defaults to read-only preflight. --smoke may start only one exact-owned
sidecar on 127.0.0.1:18013 if memory policy approves. Never touches the
existing Codex/Claude/LiteLLM/Web Bridge or their ports.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = Path("/Users/jerson/AI")
sys.path.insert(0, str(ROOT / "control-plane/src"))
from local_ai_control.services.models import MemoryPreflight

PORT = 18013
EXPECTED_MEMORY_GIB = 34
MAX_SWAP_GROWTH_GIB = 2
MAX_ABSOLUTE_SWAP_GIB = 6
PYTHON = ROOT / "runtime/qwen38-venv/bin/python"
SIDECAR = ROOT / "control-plane/scripts/qwen38-sidecar.py"
MODEL = ROOT / "models/qwen38-27b-8bit"
REPORT_ROOT = ROOT / "runtime/direct-work-validation/qwen38-codex-api"
MARKER = "QWEN38_LOCAL_API_OK"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def test_port_is_available():
    # Only the dedicated test port is checked. Does not contact any other port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", PORT))


def owned_stop(proc):
    if proc.poll() is not None:
        return True
    if os.getpgid(proc.pid) != proc.pid:
        raise RuntimeError("UNVERIFIED_PROCESS_GROUP")
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=12)
    except subprocess.TimeoutExpired:
        if os.getpgid(proc.pid) != proc.pid:
            raise RuntimeError("PROCESS_GROUP_CHANGED")
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=8)
    return True


def read_json(url, data=None, timeout=3):
    request = urllib.request.Request(
        url,
        data=None if data is None else json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="Start the existing Qwen sidecar if preflight passes")
    args = ap.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = REPORT_ROOT / run_id
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(directory, 0o700)
    report = {
        "schema": "qwen38-codex-api-admission-v1",
        "started_at": timestamp(),
        "model": str(MODEL),
        "workload": "REPRESENTATIVE_WORKLOAD",
        "mode": "SMOKE" if args.smoke else "PREFLIGHT_ONLY",
        "expected_memory_gib": EXPECTED_MEMORY_GIB,
        "test_port": PORT,
        "protected_ports_accessed": False,
        "existing_codex_config_changed": False,
        "existing_litellm_changed": False,
        "existing_web_bridge_changed": False,
        "model_files_changed": False,
        "codex_desktop_tool_e2e": "NOT_TESTED",
        "codex_tool_protocol_of_existing_sidecar": "NOT_SUPPORTED",
        "result": "NOT_RUN",
    }

    proc = None
    samples = []
    stop_event = threading.Event()
    violation = [None]
    monitor_thread = None

    def monitor(baseline_swap):
        preflight = MemoryPreflight()
        while not stop_event.is_set() and proc.poll() is None:
            try:
                s = preflight.probe()
                growth = s.swap_used_gib - baseline_swap
                samples.append({
                    "time": timestamp(), "pressure": s.pressure,
                    "swap_used_gib": s.swap_used_gib,
                    "swap_growth_gib": round(growth, 3),
                    "reclaimable_gib": s.reclaimable_gib,
                })
                if s.pressure == "CRITICAL":
                    violation[0] = "MEMORY_PRESSURE_CRITICAL"
                elif growth > MAX_SWAP_GROWTH_GIB:
                    violation[0] = "SWAP_GROWTH_LIMIT"
                elif s.swap_used_gib > MAX_ABSOLUTE_SWAP_GIB:
                    violation[0] = "SWAP_ABSOLUTE_LIMIT"
                if violation[0]:
                    owned_stop(proc)
                    return
            except Exception as exc:
                violation[0] = "MONITOR_ERROR:" + type(exc).__name__
                try:
                    owned_stop(proc)
                except Exception:
                    pass
                return
            stop_event.wait(1)

    try:
        if not PYTHON.is_file() or not SIDECAR.is_file() or not MODEL.is_dir():
            report["result"] = "DEPENDENCY_MISSING"
            report["reason"] = {
                "python": PYTHON.is_file(), "sidecar": SIDECAR.is_file(),
                "model": MODEL.is_dir(),
            }
            return 3

        try:
            test_port_is_available()
        except OSError:
            report["result"] = "TEST_PORT_UNAVAILABLE"
            return 3

        admission = MemoryPreflight().check(EXPECTED_MEMORY_GIB)
        report["preflight"] = asdict(admission)
        print("PREFLIGHT_ALLOWED =", admission.allowed)
        print("PREFLIGHT_REASON =", admission.reason)
        print("RECLAIMABLE_GIB =", admission.snapshot.reclaimable_gib)
        print("SWAP_GIB =", admission.snapshot.swap_used_gib)
        if not admission.allowed:
            report["result"] = "RESOURCE_BLOCKED"
            return 2

        if not args.smoke:
            report["result"] = "PREFLIGHT_PASS_ONLY"
            return 0

        # The preflight is repeated immediately before process creation.
        admission = MemoryPreflight().check(EXPECTED_MEMORY_GIB)
        if not admission.allowed:
            report["result"] = "RESOURCE_BLOCKED"
            report["preflight"] = asdict(admission)
            return 2
        test_port_is_available()

        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        with (directory / "worker.log").open("wb") as log:
            proc = subprocess.Popen(
                [str(PYTHON), str(SIDECAR), "--port", str(PORT),
                 "--model-path", str(MODEL)],
                cwd=str(ROOT),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        if os.getpgid(proc.pid) != proc.pid:
            raise RuntimeError("PROCESS_OWNERSHIP_UNVERIFIED")
        report["owned_worker_pid"] = proc.pid
        monitor_thread = threading.Thread(
            target=monitor, args=(admission.snapshot.swap_used_gib,), daemon=True
        )
        monitor_thread.start()

        deadline = time.monotonic() + 150
        healthy = False
        while time.monotonic() < deadline and proc.poll() is None and not violation[0]:
            try:
                h = read_json(f"http://127.0.0.1:{PORT}/health", timeout=2)
                healthy = h.get("status") == "healthy"
                if healthy:
                    report["health"] = h
                    break
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        if not healthy or violation[0]:
            report["result"] = "RESOURCE_BLOCKED" if violation[0] else "HEALTH_FAILED"
            report["reason"] = violation[0] or "MODEL_LOAD_OR_HEALTH_TIMEOUT"
            return 4

        payload = read_json(
            f"http://127.0.0.1:{PORT}/v1/responses",
            {"model": "mlx-community/Qwen3.8-27B-8bit",
             "input": "Reply with exactly this marker: " + MARKER,
             "max_output_tokens": 128},
            timeout=240,
        )
        response_text = str(payload.get("output_text", "")).strip()
        report["response_status"] = payload.get("status")
        report["response_text"] = response_text[:500]
        report["api_text_response_pass"] = (
            payload.get("status") == "completed" and MARKER in response_text
        )
        report["result"] = "API_TEXT_PASS" if report["api_text_response_pass"] else "API_TEXT_FAIL"
        if violation[0]:
            report["result"] = "RESOURCE_BLOCKED"
            report["reason"] = violation[0]
        return 0 if report["result"] == "API_TEXT_PASS" else 5
    except KeyboardInterrupt:
        report["result"] = "OWNER_INTERRUPTED"
        return 130
    except Exception as exc:
        report["result"] = "ERROR"
        report["reason"] = type(exc).__name__ + ": " + str(exc)[:300]
        return 6
    finally:
        stop_event.set()
        if proc is not None:
            try:
                owned_stop(proc)
                report["cleanup_ok"] = proc.poll() is not None
            except Exception as exc:
                report["cleanup_ok"] = False
                report["cleanup_error"] = type(exc).__name__
        else:
            report["model_started"] = False
            report["cleanup_ok"] = True
        if monitor_thread:
            monitor_thread.join(timeout=3)
        report["resource_samples"] = samples
        report["resource_violation"] = violation[0]
        report["finished_at"] = timestamp()
        (directory / "result.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print("RESULT =", report["result"])
        print("REPORT =", directory / "result.json")
        print("CLEANUP_OK =", report["cleanup_ok"])
        print("CODEX_TOOL_E2E =", report["codex_desktop_tool_e2e"])
        print("PROTECTED_PORTS_ACCESSED = False")


if __name__ == "__main__":
    sys.exit(main())
