from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from local_ai_control.services.supervisor import (
    CONTROL_PLANE_PYTHON, SUPERVISOR_RUNTIME, ensure_private_directory, ensure_private_file,
)

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


def _ps(pid: int, field: str) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", f"{field}="],
            capture_output=True, text=True, shell=False, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def process_snapshot(pid: int) -> ProcessIdentity | None:
    command = _ps(pid, "command")
    start = _ps(pid, "lstart")
    if not command or not start:
        return None
    try:
        argv = tuple(shlex.split(command))
    except ValueError:
        return None
    if not argv:
        return None
    return ProcessIdentity(int(pid), argv[0], argv, start)


def expected_snapshot(pid: int) -> ProcessIdentity | None:
    snapshot = process_snapshot(pid)
    if snapshot is None:
        return None
    if snapshot.argv != EXPECTED_ARGV:
        return None
    if snapshot.executable != str(CONTROL_PLANE_PYTHON):
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
    if set(data) != {"pid", "executable", "argv", "start_identity"}:
        raise ValueError("invalid process identity schema")
    pid = int(data["pid"])
    executable = str(data["executable"])
    argv = tuple(str(item) for item in data["argv"])
    start_identity = str(data["start_identity"])
    if pid <= 0 or not start_identity or argv != EXPECTED_ARGV or executable != str(CONTROL_PLANE_PYTHON):
        raise ValueError("process identity does not match supervisor signature")
    return ProcessIdentity(pid, executable, argv, start_identity)


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


def cli() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--pid", type=int, required=True)
    capture_parser.add_argument("--file", type=Path, default=IDENTITY_FILE)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--file", type=Path, default=IDENTITY_FILE)
    pid_parser = sub.add_parser("pid")
    pid_parser.add_argument("--file", type=Path, default=IDENTITY_FILE)
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.pid, args.file)
        return 0
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
