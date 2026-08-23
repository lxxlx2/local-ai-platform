"""Durable, serial, resumable Hugging Face model download queue."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import time
from typing import Callable

REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_ROOT = Path("/Users/jerson/AI")
DEFAULT_CONFIG = DEFAULT_ROOT / "config/model-download-queue-v0.1.json"
DEFAULT_RUNTIME = DEFAULT_ROOT / "runtime/model-downloads"
DEFAULT_MODELS = DEFAULT_ROOT / "models"
LABEL = "local-ai.model-download-queue"


@dataclass(frozen=True)
class DownloadSpec:
    id: str
    role: str
    repo: str
    revision: str
    local_dir: Path
    expected_bytes: int
    license: str
    runtime: str
    include: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueueConfig:
    models: tuple[DownloadSpec, ...]
    max_attempts: int
    reserve_bytes: int


@dataclass(frozen=True)
class StorageBytes:
    payload_bytes: int
    partial_cache_bytes: int


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_queue_config(path: Path = DEFAULT_CONFIG, *, models_root: Path = DEFAULT_MODELS) -> QueueConfig:
    payload = json.loads(path.read_text())
    if payload.get("serial_only") is not True:
        raise ValueError("download queue must be serial")
    max_attempts = int(payload.get("max_attempts", 0))
    reserve_bytes = int(payload.get("reserve_bytes", 0))
    if not 1 <= max_attempts <= 5 or reserve_bytes < 0:
        raise ValueError("invalid bounded queue settings")
    root = models_root.resolve()
    models: list[DownloadSpec] = []
    seen: set[str] = set()
    for raw in payload.get("models", []):
        local_dir = Path(raw["local_dir"]).resolve()
        if local_dir == root or root not in local_dir.parents:
            raise ValueError("model local_dir escapes models root")
        if raw["id"] in seen or not REVISION_RE.fullmatch(raw["revision"]):
            raise ValueError("duplicate id or unpinned revision")
        if "/" not in raw["repo"] or int(raw["expected_bytes"]) <= 0:
            raise ValueError("invalid model metadata")
        includes = tuple(raw.get("include", ()))
        if any(not item or item.startswith("/") or ".." in item for item in includes):
            raise ValueError("unsafe include pattern")
        seen.add(raw["id"])
        models.append(DownloadSpec(raw["id"], raw["role"], raw["repo"], raw["revision"], local_dir, int(raw["expected_bytes"]), raw["license"], raw["runtime"], includes))
    if not models:
        raise ValueError("empty queue")
    return QueueConfig(tuple(models), max_attempts, reserve_bytes)


def storage_bytes(path: Path) -> StorageBytes:
    payload = partial = 0
    if not path.exists():
        return StorageBytes(0,0)
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                size=item.stat().st_size; relative=item.relative_to(path)
                if item.name==".local-ai-download-complete.json":
                    continue
                if item.name.endswith(".incomplete"):
                    partial+=size
                elif ".cache" not in relative.parts:
                    payload+=size
        except FileNotFoundError:
            continue
    return StorageBytes(payload,partial)


def directory_bytes(path: Path) -> int:
    """Compatibility helper: completed payload only, never partial cache."""
    return storage_bytes(path).payload_bytes


class ModelDownloadQueue:
    def __init__(
        self,
        config: QueueConfig,
        runtime_dir: Path = DEFAULT_RUNTIME,
        *,
        downloader: Callable[[DownloadSpec, Path], int] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.runtime_dir = runtime_dir
        self.state_path = runtime_dir / "state.json"
        self.log_path = runtime_dir / "queue.log"
        self.lock_path = runtime_dir / "queue.lock"
        self.pid_path = runtime_dir / "queue.pid"
        self.downloader = downloader or self._download
        self.sleeper = sleeper
        self.state: dict[str, object] = {}

    def _atomic_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, path)

    def _log(self, event: str, model_id: str = "-") -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as handle:
            handle.write(f"{utc_now()} | {event} | {model_id}\n")

    def _marker_path(self, spec: DownloadSpec) -> Path:
        return spec.local_dir / ".local-ai-download-complete.json"

    def _payload_manifest(self,spec: DownloadSpec) -> list[dict[str,object]]:
        files=[]
        if not spec.local_dir.exists(): return files
        for item in sorted(spec.local_dir.rglob("*")):
            if not item.is_file() or item.is_symlink(): continue
            relative=item.relative_to(spec.local_dir)
            if ".cache" in relative.parts or item.name.endswith(".incomplete") or item.name==".local-ai-download-complete.json": continue
            files.append({"path":relative.as_posix(),"size":item.stat().st_size})
        return files

    def _is_complete(self, spec: DownloadSpec) -> bool:
        marker = self._marker_path(spec)
        try:
            payload = json.loads(marker.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        if payload.get("repo") != spec.repo or payload.get("revision") != spec.revision or payload.get("expected_bytes") != spec.expected_bytes:
            return False
        manifest=payload.get("files")
        if not isinstance(manifest,list) or not manifest:
            return False
        for entry in manifest:
            if not isinstance(entry,dict) or set(entry)!={"path","size"} or not isinstance(entry["path"],str) or not isinstance(entry["size"],int):
                return False
            candidate=(spec.local_dir/entry["path"]).resolve()
            if spec.local_dir.resolve() not in candidate.parents or not candidate.is_file() or candidate.stat().st_size!=entry["size"]:
                return False
        if manifest!=self._payload_manifest(spec):
            return False
        return self._snapshot_valid(spec)

    def _snapshot_valid(self, spec: DownloadSpec) -> bool:
        indexes=list(spec.local_dir.rglob("*.safetensors.index.json"))
        for index in indexes:
            try:
                weight_map = json.loads(index.read_text()).get("weight_map", {})
            except (json.JSONDecodeError, OSError):
                return False
            if not weight_map or any(not (index.parent / name).is_file() for name in set(weight_map.values())):
                return False
        return bool(self._payload_manifest(spec)) and storage_bytes(spec.local_dir).payload_bytes >= int(spec.expected_bytes * 0.98)

    def _write_marker(self, spec: DownloadSpec) -> None:
        self._atomic_json(self._marker_path(spec), {"repo": spec.repo, "revision": spec.revision, "expected_bytes": spec.expected_bytes, "completed_at": utc_now(),"files":self._payload_manifest(spec)})

    def _disk_ready(self, spec: DownloadSpec) -> bool:
        current = storage_bytes(spec.local_dir).payload_bytes
        remaining = max(spec.expected_bytes - current, 0)
        return shutil.disk_usage(spec.local_dir.parent if spec.local_dir.parent.exists() else DEFAULT_ROOT).free >= remaining + self.config.reserve_bytes

    def _download(self, spec: DownloadSpec, log_path: Path) -> int:
        command = ["/Users/jerson/AI/runtime/qwen38-venv/bin/hf", "download", spec.repo, "--revision", spec.revision, "--local-dir", str(spec.local_dir)]
        for pattern in spec.include:
            command.extend(["--include", pattern])
        environment = os.environ.copy()
        environment.update({"HF_HUB_DISABLE_XET": "1", "HF_HUB_DOWNLOAD_TIMEOUT": "120", "HF_HUB_ETAG_TIMEOUT": "30"})
        spec.local_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as handle:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, env=environment, shell=False)
        return result.returncode

    def _snapshot_state(self, current: DownloadSpec | None, statuses: dict[str, dict[str, object]], queue_state: str) -> None:
        self.state = {
            "schema_version": "0.1", "state": queue_state, "pid": os.getpid(), "updated_at": utc_now(),
            "current_model": current.id if current else None, "current_repo": current.repo if current else None,
            "current_local_dir": str(current.local_dir) if current else None,
            "current_payload_bytes": storage_bytes(current.local_dir).payload_bytes if current else 0,
            "current_partial_cache_bytes": storage_bytes(current.local_dir).partial_cache_bytes if current else 0,
            "models": statuses,
            "completed": [key for key, value in statuses.items() if value["status"] == "COMPLETED"],
            "failed": [key for key, value in statuses.items() if value["status"] == "FAILED"],
            "pending": [key for key, value in statuses.items() if value["status"] == "PENDING"],
        }
        self._atomic_json(self.state_path, self.state)

    def run(self) -> str:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        lock = self.lock_path.open("a+")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._log("SINGLETON_ALREADY_RUNNING")
            return "ALREADY_RUNNING"
        self.pid_path.write_text(f"{os.getpid()}\n")
        statuses = {spec.id: {"status": "COMPLETED" if self._is_complete(spec) else "PENDING", "repo": spec.repo, "local_dir": str(spec.local_dir), "retry_count": 0, "exit_code": None, "started_at": None, "finished_at": None} for spec in self.config.models}
        self._snapshot_state(None, statuses, "RUNNING")
        self._log("QUEUE_STARTED")
        try:
            for spec in self.config.models:
                item = statuses[spec.id]
                if item["status"] == "COMPLETED":
                    self._log("SKIP_VERIFIED_COMPLETE", spec.id)
                    continue
                if not self._disk_ready(spec):
                    item.update({"status": "FAILED", "finished_at": utc_now(), "error_category": "INSUFFICIENT_DISK"})
                    self._log("FAILED_INSUFFICIENT_DISK", spec.id)
                    self._snapshot_state(None, statuses, "RUNNING")
                    continue
                item.update({"status": "DOWNLOADING", "started_at": utc_now()})
                self._snapshot_state(spec, statuses, "RUNNING")
                self._log("DOWNLOAD_STARTED", spec.id)
                model_log = self.runtime_dir / f"{spec.id}.log"
                for attempt in range(1, self.config.max_attempts + 1):
                    item["retry_count"] = attempt - 1
                    code = self.downloader(spec, model_log)
                    item["exit_code"] = code
                    self._snapshot_state(spec, statuses, "RUNNING")
                    if code == 0 and self._snapshot_valid(spec):
                        self._write_marker(spec)
                        item.update({"status": "COMPLETED", "finished_at": utc_now()})
                        self._log("DOWNLOAD_COMPLETED", spec.id)
                        break
                    if attempt < self.config.max_attempts:
                        self._log(f"DOWNLOAD_RETRY_{attempt}", spec.id)
                        self.sleeper((30, 120, 300)[min(attempt - 1, 2)])
                else:
                    item.update({"status": "FAILED", "finished_at": utc_now(), "error_category": "DOWNLOAD_FAILED"})
                    self._log("DOWNLOAD_FAILED", spec.id)
                self._snapshot_state(None, statuses, "RUNNING")
            final = "COMPLETED_WITH_FAILURES" if any(value["status"] == "FAILED" for value in statuses.values()) else "COMPLETED"
            self._snapshot_state(None, statuses, final)
            self._log(final)
            return final
        finally:
            self.pid_path.unlink(missing_ok=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()


def write_launch_plist(path: Path = DEFAULT_RUNTIME / f"{LABEL}.plist") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": ["/Users/jerson/AI/runtime/control-plane-venv/bin/python", "/Users/jerson/AI/control-plane/scripts/model-download-queue.py", "--run"],
        "WorkingDirectory": "/Users/jerson/AI", "RunAtLoad": True, "KeepAlive": False,
        "ProcessType": "Background", "StandardOutPath": str(DEFAULT_RUNTIME / "launch.stdout.log"),
        "StandardErrorPath": str(DEFAULT_RUNTIME / "launch.stderr.log"),
        "EnvironmentVariables": {"HF_HUB_DISABLE_XET": "1", "HF_HUB_DOWNLOAD_TIMEOUT": "120", "HF_HUB_ETAG_TIMEOUT": "30"},
    }
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle)
    os.replace(temporary, path)
    return path


def bounded_status(runtime_dir: Path = DEFAULT_RUNTIME) -> str:
    try:
        state = json.loads((runtime_dir / "state.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "MODEL_DOWNLOAD_QUEUE\nstate: NOT_STARTED\npid: -\n"
    current_dir = Path(state["current_local_dir"]) if state.get("current_local_dir") else None
    live_storage = storage_bytes(current_dir) if current_dir else StorageBytes(0,0)
    shard_text = "-"
    if current_dir:
        indexes = list(current_dir.glob("*.safetensors.index.json"))
        if len(indexes) == 1:
            try:
                expected = set(json.loads(indexes[0].read_text()).get("weight_map", {}).values())
                if expected:
                    shard_text = f"{sum((current_dir / name).is_file() for name in expected)}/{len(expected)}"
            except (json.JSONDecodeError, OSError):
                pass
    lines = ["MODEL_DOWNLOAD_QUEUE", f"state: {state.get('state')}", f"pid: {state.get('pid')}", "", "CURRENT_MODEL", f"id: {state.get('current_model') or '-'}", f"repo: {state.get('current_repo') or '-'}", f"local_dir: {state.get('current_local_dir') or '-'}", f"payload_gib: {live_storage.payload_bytes / 1024**3:.3f}", f"partial_cache_gib: {live_storage.partial_cache_bytes / 1024**3:.3f}", f"completed_shards: {shard_text}", f"started_at: {next((v.get('started_at') for k, v in state.get('models', {}).items() if k == state.get('current_model')), None) or '-'}", "", f"COMPLETED: {', '.join(state.get('completed', [])) or '-'}", f"FAILED: {', '.join(state.get('failed', [])) or '-'}", f"PENDING: {', '.join(state.get('pending', [])) or '-'}", "", "RECENT_LOG"]
    try:
        recent = (runtime_dir / "queue.log").read_text().splitlines()[-5:]
    except OSError:
        recent = []
    lines.extend(recent or ["-"])
    return "\n".join(lines) + "\n"
