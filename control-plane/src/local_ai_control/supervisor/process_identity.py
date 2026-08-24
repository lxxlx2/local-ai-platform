from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from local_ai_control.services.supervisor import (
    CONTROL_PLANE_PYTHON, SUPERVISOR_RUNTIME, ensure_private_directory, ensure_private_file,
)
from local_ai_control.services.heavy_process_identity import expected_spawn_identity

EXPECTED_ARGV = (
    str(CONTROL_PLANE_PYTHON),
    "-m",
    "local_ai_control.supervisor.app",
    "daemon",
)
IDENTITY_FILE = SUPERVISOR_RUNTIME / "supervisor.identity.json"


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    executable: str
    argv: tuple[str, ...]
    start_identity: str


def _process_text(argv: list[str]) -> str | None:
    try:
        result = subprocess.run(
            argv,
            capture_output=True, text=True, shell=False, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def process_snapshot(pid: int) -> ProcessIdentity | None:
    if isinstance(pid, bool):
        return None
    try:
        exact_pid = int(pid)
    except (TypeError, ValueError):
        return None
    if exact_pid <= 0:
        return None
    command = _process_text(["/bin/ps", "-ww", "-p", str(exact_pid), "-o", "command="])
    start = _process_text(["/bin/ps", "-p", str(exact_pid), "-o", "lstart="])
    executable = _process_text(["/usr/bin/proc_pidpath", str(exact_pid)])
    if executable is None:
        executable = _process_text(["/bin/ps", "-p", str(exact_pid), "-o", "comm="])
    if not command or not start or not executable:
        return None
    try:
        argv = tuple(shlex.split(command))
    except ValueError:
        return None
    if not argv:
        return None
    return ProcessIdentity(exact_pid, str(Path(executable).resolve()), argv, start)


def _matches_expected_spawn(identity: ProcessIdentity) -> bool:
    """Match only the exact Supervisor spawn under reviewed path normalization."""
    return expected_spawn_identity(identity, EXPECTED_ARGV)


def expected_snapshot(pid: int) -> ProcessIdentity | None:
    snapshot = process_snapshot(pid)
    if snapshot is None or not _matches_expected_spawn(snapshot):
        return None
    return snapshot


def write_identity(path: Path, identity: ProcessIdentity) -> None:
    target = Path(path)
    ensure_private_directory(target.parent)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    payload = asdict(identity)
    payload["argv"] = list(identity.argv)
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        ensure_private_file(tmp)
        os.replace(tmp, target)
        ensure_private_file(target)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_identity(path: Path) -> ProcessIdentity:
    target = Path(path)
    ensure_private_file(target)
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"pid", "executable", "argv", "start_identity"}:
        raise ValueError("invalid process identity schema")
    if (not isinstance(data["pid"], int) or isinstance(data["pid"], bool) or data["pid"] <= 0
            or not isinstance(data["executable"], str) or not data["executable"]
            or not isinstance(data["argv"], list) or not data["argv"]
            or not all(isinstance(item, str) and item for item in data["argv"])
            or not isinstance(data["start_identity"], str) or not data["start_identity"]):
        raise ValueError("invalid process identity schema")
    identity = ProcessIdentity(data["pid"], data["executable"], tuple(data["argv"]), data["start_identity"])
    if not _matches_expected_spawn(identity):
        raise ValueError("process identity does not match supervisor signature")
    return identity


def identity_status(path: Path) -> tuple[str, int | None]:
    target = Path(path)
    if not target.exists():
        return "MISSING", None
    try:
        saved = read_identity(target)
    except (OSError, ValueError, json.JSONDecodeError, PermissionError):
        return "INVALID", None
    current = process_snapshot(saved.pid)
    if current is None:
        return "DEAD", saved.pid
    if current == saved:
        return "MATCH", saved.pid
    return "MISMATCH", saved.pid


def capture(pid: int, path: Path) -> ProcessIdentity:
    snapshot = expected_snapshot(pid)
    if snapshot is None:
        raise RuntimeError("process does not match exact supervisor argv/executable identity")
    write_identity(path, snapshot)
    return snapshot


def classify_started_process(pid: int) -> tuple[str, str | None]:
    snapshot = process_snapshot(pid)
    if snapshot is None:
        return "DEAD", None
    if not _matches_expected_spawn(snapshot):
        return "MISMATCH", None
    return "EXPECTED", snapshot.start_identity


def start_identity(pid: int) -> str:
    snapshot = expected_snapshot(pid)
    if snapshot is None:
        raise RuntimeError("process does not match exact supervisor argv/executable identity")
    return snapshot.start_identity


def cleanup_started_process(pid: int, expected_start_identity: str, path: Path, wait_seconds: float = 5.0) -> str:
    current = process_snapshot(pid)
    if current is None:
        Path(path).unlink(missing_ok=True)
        return "ALREADY_DEAD"
    if not _matches_expected_spawn(current) or current.start_identity != expected_start_identity:
        return "ORPHAN_RECONCILIATION_REQUIRED"
    try:
        saved = read_identity(path) if Path(path).exists() else None
    except Exception:
        saved = None
    if saved is not None and saved != current:
        return "ORPHAN_RECONCILIATION_REQUIRED"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        Path(path).unlink(missing_ok=True)
        return "ALREADY_DEAD"
    deadline = time.monotonic() + max(0.1, wait_seconds)
    while time.monotonic() < deadline:
        if process_snapshot(pid) is None:
            Path(path).unlink(missing_ok=True)
            return "TERMINATED"
        time.sleep(0.05)
    return "ORPHAN_RECONCILIATION_REQUIRED"


def cli() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--pid", type=int, required=True)
    capture_parser.add_argument("--file", type=Path, default=IDENTITY_FILE)
    start_parser = sub.add_parser("start-identity")
    start_parser.add_argument("--pid", type=int, required=True)
    cleanup_parser = sub.add_parser("cleanup-start")
    cleanup_parser.add_argument("--pid", type=int, required=True)
    cleanup_parser.add_argument("--start-identity", required=True)
    cleanup_parser.add_argument("--file", type=Path, default=IDENTITY_FILE)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--file", type=Path, default=IDENTITY_FILE)
    pid_parser = sub.add_parser("pid")
    pid_parser.add_argument("--file", type=Path, default=IDENTITY_FILE)
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.pid, args.file)
        return 0
    if args.command == "start-identity":
        status, identity = classify_started_process(args.pid)
        if status == "EXPECTED" and identity is not None:
            print(identity)
            return 0
        print(status)
        return 3 if status == "DEAD" else 4
    if args.command == "cleanup-start":
        status = cleanup_started_process(args.pid, args.start_identity, args.file)
        print(status)
        return 0 if status in {"TERMINATED", "ALREADY_DEAD"} else 4
    status, pid = identity_status(args.file)
    if args.command == "pid":
        if pid is not None:
            print(pid)
            return 0
        return 3
    print(status)
    return {"MATCH": 0, "MISSING": 3, "DEAD": 3, "INVALID": 4, "MISMATCH": 4}[status]


if __name__ == "__main__":
    raise SystemExit(cli())
