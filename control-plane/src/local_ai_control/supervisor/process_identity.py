from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from local_ai_control.services.supervisor import (
    CONTROL_PLANE_PYTHON,
    SUPERVISOR_RUNTIME,
    ensure_private_directory,
    ensure_private_file,
)

PIDFILE = SUPERVISOR_RUNTIME / "supervisor.pid"
IDENTITYFILE = SUPERVISOR_RUNTIME / "supervisor.identity.json"
EXPECTED_ARGV = (
    str(CONTROL_PLANE_PYTHON),
    "-m",
    "local_ai_control.supervisor.app",
    "daemon",
)


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    executable: str
    argv: tuple[str, ...]
    start_identity: str


def _ps_value(pid: int, field: str) -> str:
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", f"{field}="],
        capture_output=True,
        text=True,
        shell=False,
        timeout=3,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def current_identity(pid: int) -> ProcessIdentity | None:
    command = _ps_value(pid, "command")
    start = _ps_value(pid, "lstart")
    if not command or not start:
        return None
    try:
        argv = tuple(shlex.split(command))
    except ValueError:
        return None
    if not argv:
        return None
    executable = os.path.realpath(argv[0])
    return ProcessIdentity(int(pid), executable, argv, start)


def expected_identity(pid: int) -> ProcessIdentity | None:
    current = current_identity(pid)
    if current is None:
        return None
    expected_executable = os.path.realpath(str(CONTROL_PLANE_PYTHON))
    if current.executable != expected_executable or current.argv != EXPECTED_ARGV:
        return None
    return current


def identities_match(saved: ProcessIdentity, current: ProcessIdentity | None) -> bool:
    if current is None:
        return False
    return (
        saved.pid == current.pid
        and saved.executable == current.executable
        and saved.argv == current.argv
        and saved.start_identity == current.start_identity
    )


def _write_private(path: Path, text: str) -> None:
    ensure_private_directory(path.parent)
    temp = path.with_name(path.name + f".tmp.{os.getpid()}")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        ensure_private_file(path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_identity(pid: int, pidfile: Path = PIDFILE, identityfile: Path = IDENTITYFILE) -> ProcessIdentity:
    identity = expected_identity(pid)
    if identity is None:
        raise RuntimeError("process does not match exact supervisor identity")
    _write_private(identityfile, json.dumps(asdict(identity), sort_keys=True, separators=(",", ":")))
    _write_private(pidfile, f"{pid}\n")
    return identity


def _load_saved(pidfile: Path, identityfile: Path) -> tuple[int | None, ProcessIdentity | None]:
    if not pidfile.exists():
        return None, None
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return None, None
    if not identityfile.exists():
        return pid, None
    try:
        data = json.loads(identityfile.read_text())
        saved = ProcessIdentity(
            int(data["pid"]),
            str(data["executable"]),
            tuple(str(value) for value in data["argv"]),
            str(data["start_identity"]),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return pid, None
    return pid, saved


def verify_identity(pidfile: Path = PIDFILE, identityfile: Path = IDENTITYFILE) -> tuple[bool, str]:
    pid, saved = _load_saved(pidfile, identityfile)
    if pid is None:
        return False, "MISSING"
    current = current_identity(pid)
    if current is None:
        return False, "DEAD"
    if saved is None:
        return False, "IDENTITY_MISSING"
    if saved.pid != pid:
        return False, "PID_MISMATCH"
    if not identities_match(saved, current):
        return False, "IDENTITY_MISMATCH"
    if current.executable != os.path.realpath(str(CONTROL_PLANE_PYTHON)) or current.argv != EXPECTED_ARGV:
        return False, "ARGV_MISMATCH"
    return True, "OK"


def cleanup_identity(pidfile: Path = PIDFILE, identityfile: Path = IDENTITYFILE) -> None:
    pidfile.unlink(missing_ok=True)
    identityfile.unlink(missing_ok=True)


def cli() -> int:
    parser = argparse.ArgumentParser(description="Exact Workflow Supervisor process identity")
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write")
    write.add_argument("--pid", required=True, type=int)
    sub.add_parser("verify")
    sub.add_parser("cleanup")
    args = parser.parse_args()
    if args.command == "write":
        try:
            write_identity(args.pid)
        except Exception:
            return 3
        return 0
    if args.command == "cleanup":
        cleanup_identity()
        return 0
    valid, reason = verify_identity()
    if valid:
        print("IDENTITY=OK")
        return 0
    print(f"IDENTITY={reason}")
    return 2 if reason in {"MISSING", "DEAD"} else 3


if __name__ == "__main__":
    raise SystemExit(cli())
