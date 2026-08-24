"""Exact, private process identities for platform-owned heavy runtimes.

This module is intentionally separate from the Workflow Supervisor identity
contract.  A saved PID is never ownership proof by itself: executable, argv,
and process start identity must all still match.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Callable


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    executable: str
    argv: tuple[str, ...]
    start_identity: str


def _run_text(argv: list[str]) -> str:
    result=subprocess.run(argv,capture_output=True,text=True,shell=False,timeout=5,check=False)
    if result.returncode:
        raise ProcessLookupError("process metadata unavailable")
    return result.stdout.strip()


def process_snapshot(pid: int) -> ProcessIdentity | None:
    """Read one PID without substring matching or broad process discovery."""
    if not isinstance(pid,int) or pid<=0:
        return None
    try:
        start=_run_text(["/bin/ps","-p",str(pid),"-o","lstart="])
        command=_run_text(["/bin/ps","-ww","-p",str(pid),"-o","command="])
        try:
            executable=_run_text(["/usr/bin/proc_pidpath",str(pid)])
        except (FileNotFoundError,ProcessLookupError,subprocess.SubprocessError):
            executable=_run_text(["/bin/ps","-p",str(pid),"-o","comm="])
        argv=tuple(shlex.split(command))
    except (FileNotFoundError,ProcessLookupError,ValueError,subprocess.SubprocessError):
        return None
    if not start or not executable or not argv:
        return None
    return ProcessIdentity(pid,str(Path(executable).resolve()),argv,start)


def write_identity(path: Path, identity: ProcessIdentity) -> None:
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(path.parent,0o700)
    temporary=path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload=asdict(identity); payload["argv"]=list(identity.argv)
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,"w") as handle:
        json.dump(payload,handle,ensure_ascii=False,indent=2); handle.write("\n")
    os.replace(temporary,path); os.chmod(path,0o600)


def read_identity(path: Path) -> ProcessIdentity | None:
    try:
        raw=json.loads(Path(path).read_text())
        if set(raw)!={"pid","executable","argv","start_identity"}:
            return None
        if not isinstance(raw["pid"],int) or raw["pid"]<=0 or not isinstance(raw["executable"],str):
            return None
        if not isinstance(raw["argv"],list) or not raw["argv"] or not all(isinstance(item,str) and item for item in raw["argv"]):
            return None
        if not isinstance(raw["start_identity"],str) or not raw["start_identity"]:
            return None
        return ProcessIdentity(raw["pid"],raw["executable"],tuple(raw["argv"]),raw["start_identity"])
    except (FileNotFoundError,OSError,json.JSONDecodeError,TypeError,ValueError):
        return None


def identity_status(path: Path, *, snapshot: Callable[[int],ProcessIdentity|None]=process_snapshot) -> tuple[str,int|None]:
    path=Path(path)
    if not path.exists():
        return "MISSING",None
    saved=read_identity(path)
    if saved is None:
        return "INVALID",None
    current=snapshot(saved.pid)
    if current is None:
        return "DEAD",saved.pid
    if current==saved:
        return "MATCH",saved.pid
    return "MISMATCH",saved.pid


def listener_pids(port: int) -> tuple[int, ...]:
    """Return exact LISTEN PIDs for one fixed TCP port using argv form."""
    result=subprocess.run(
        ["/usr/sbin/lsof","-nP","-a",f"-iTCP:{int(port)}","-sTCP:LISTEN","-Fp"],
        capture_output=True,text=True,shell=False,timeout=5,check=False,
    )
    if result.returncode not in {0,1}:
        raise RuntimeError("listener inspection failed")
    return tuple(sorted({int(line[1:]) for line in result.stdout.splitlines() if line.startswith("p") and line[1:].isdigit()}))


def expected_identity(snapshot: ProcessIdentity, executable: str, argv: tuple[str,...]) -> bool:
    expected_executable=str(Path(executable).resolve())
    normalized_argv=list(argv)
    if normalized_argv:
        normalized_argv[0]=str(Path(normalized_argv[0]).resolve())
    actual_argv=list(snapshot.argv)
    if actual_argv:
        actual_argv[0]=str(Path(actual_argv[0]).resolve())
    return snapshot.executable==expected_executable and tuple(actual_argv)==tuple(normalized_argv)
