"""Owner-only RAW Qwen inference boundary.

The model is permissive text generation, not an authority source.  This module
intentionally exposes no model-callable tool interface: filesystem, shell, Git,
credentials, service control, package installation, downloads and egress all
remain outside the model process and provider contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from local_ai_control.domain.identity import IdentityContext, Role
from local_ai_control.services.authorization import AuthorizationDenied
from local_ai_control.services.heavy_process_identity import (
    ProcessIdentity,
    expected_spawn_identity,
    identity_status,
    listener_pids,
    process_snapshot,
    read_identity,
    write_identity,
)
from local_ai_control.services.provider_router import (
    Capability,
    InvocationPurpose,
    PrivacyMode,
    ProviderRequest,
    ProviderRouter,
    default_provider_router,
)
from local_ai_control.services.models import MemoryPreflight


RAW_PROFILE_ID = "owner-qwen38-raw-q6k"
RAW_PROVIDER_ID = "local-qwen-owner-raw"
RAW_REPO = "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF"
RAW_FILENAME = "Qwen3.8-27B-Uncensored-Q6_K.gguf"
RAW_SHA256 = "a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6"
RAW_MODEL_ROOT = Path("/Users/jerson/AI/models/qwen38-owner-raw-q6k")
RAW_RUNTIME_ROOT = Path("/Users/jerson/AI/runtime/owner-raw")
RAW_LLAMA_SERVER = Path("/opt/homebrew/bin/llama-server")
RAW_HOST = "127.0.0.1"
RAW_PORT = 8002
RAW_CONTEXT_TOKENS = 8192
RAW_MAX_OUTPUT_TOKENS = 1024
RAW_MAX_PROMPT_BYTES = 512 * 1024
RAW_START_TIMEOUT_SECONDS = 180
RAW_REQUEST_TIMEOUT_SECONDS = 90


class RawRuntimeState(StrEnum):
    NOT_DOWNLOADED = "NOT_DOWNLOADED"
    INCOMPLETE = "INCOMPLETE"
    READY = "READY"
    RUNNING = "RUNNING"
    UNHEALTHY = "UNHEALTHY"


class RawRuntimeError(RuntimeError):
    pass


class RawModelUnavailable(RawRuntimeError):
    pass


class RawProcessConflict(RawRuntimeError):
    pass


@dataclass(frozen=True)
class RawArtifactInspection:
    state: RawRuntimeState
    path: str
    integrity: str
    size_bytes: int
    incomplete_files: int


@dataclass(frozen=True)
class RawRuntimeStatus:
    state: RawRuntimeState
    model_path: str
    integrity: str
    model_size_bytes: int
    incomplete_files: int
    runtime: str
    endpoint: str
    pid: int | None
    identity_status: str
    listener_pids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        value["listener_pids"] = list(self.listener_pids)
        return value


class RawArtifactValidator:
    """Validate the one pinned GGUF without following path indirection."""

    def __init__(
        self,
        model_root: Path = RAW_MODEL_ROOT,
        *,
        filename: str = RAW_FILENAME,
        expected_sha256: str = RAW_SHA256,
        hasher: Callable[[Path], str] | None = None,
    ):
        self.model_root = Path(model_root)
        self.filename = filename
        self.expected_sha256 = expected_sha256.lower()
        self.hasher = hasher or self._sha256
        if self.filename != RAW_FILENAME or Path(self.filename).name != self.filename:
            raise ValueError("RAW GGUF filename is not the pinned artifact")
        if len(self.expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.expected_sha256):
            raise ValueError("invalid pinned RAW SHA256")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @property
    def model_path(self) -> Path:
        return self.model_root / self.filename

    def inspect(self) -> RawArtifactInspection:
        root = self.model_root
        if not root.exists():
            return RawArtifactInspection(RawRuntimeState.NOT_DOWNLOADED, str(self.model_path), "MISSING", 0, 0)
        if root.is_symlink() or not root.is_dir():
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(self.model_path), "UNSAFE_MODEL_ROOT", 0, 0)
        try:
            root_resolved = root.resolve(strict=True)
            candidate = self.model_path
            incomplete = tuple(root.rglob("*.incomplete"))
        except OSError:
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(self.model_path), "INSPECTION_FAILED", 0, 0)
        if candidate.parent.resolve() != root_resolved:
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(candidate), "PATH_ESCAPE", 0, len(incomplete))
        if incomplete:
            size = candidate.stat().st_size if candidate.exists() and not candidate.is_symlink() else 0
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(candidate), "PARTIAL_CACHE_PRESENT", size, len(incomplete))
        if not candidate.exists():
            any_payload = any(path.is_file() for path in root.rglob("*"))
            state = RawRuntimeState.INCOMPLETE if any_payload else RawRuntimeState.NOT_DOWNLOADED
            return RawArtifactInspection(state, str(candidate), "MISSING", 0, 0)
        try:
            metadata = candidate.lstat()
        except OSError:
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(candidate), "INSPECTION_FAILED", 0, 0)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(candidate), "UNSAFE_ARTIFACT", 0, 0)
        if candidate.resolve(strict=True).parent != root_resolved:
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(candidate), "PATH_ESCAPE", 0, 0)
        try:
            actual = self.hasher(candidate).lower()
        except OSError:
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(candidate), "HASH_FAILED", metadata.st_size, 0)
        if actual != self.expected_sha256:
            return RawArtifactInspection(RawRuntimeState.INCOMPLETE, str(candidate), "SHA256_MISMATCH", metadata.st_size, 0)
        return RawArtifactInspection(RawRuntimeState.READY, str(candidate), f"SHA256:{actual}", metadata.st_size, 0)


class RawLlamaProvider:
    """Text-only llama.cpp client; it has no tool or host execution channel."""

    def __init__(
        self,
        *,
        host: str = RAW_HOST,
        port: int = RAW_PORT,
        timeout: int = RAW_REQUEST_TIMEOUT_SECONDS,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ):
        if host != RAW_HOST or int(port) != RAW_PORT:
            raise ValueError("RAW provider endpoint must use the fixed loopback address")
        self.base_url = f"http://{host}:{int(port)}"
        self.timeout = int(timeout)
        self.urlopen = urlopen

    def _request(self, path: str, payload: dict[str, Any] | None = None, *, timeout: int | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if data is None else "POST",
        )
        try:
            with self.urlopen(request, timeout=timeout or self.timeout) as response:
                value = json.load(response)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
            raise RawRuntimeError("RAW llama.cpp endpoint unavailable") from error
        if not isinstance(value, dict):
            raise RawRuntimeError("RAW llama.cpp returned an invalid response")
        return value

    def health(self) -> dict[str, Any]:
        return self._request("/health", timeout=5)

    def generate(self, prompt: str, *, max_output_tokens: int = 512) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt required")
        if len(prompt.encode("utf-8")) > RAW_MAX_PROMPT_BYTES:
            raise ValueError("RAW prompt exceeds bounded request size")
        requested = int(max_output_tokens)
        if requested <= 0:
            raise ValueError("max_output_tokens must be positive")
        requested = min(requested, RAW_MAX_OUTPUT_TOKENS)
        result = self._request(
            "/v1/chat/completions",
            {
                "model": RAW_FILENAME,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": requested,
                "stream": False,
                "temperature": 0.7,
            },
        )
        try:
            text = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RawRuntimeError("RAW llama.cpp completion payload is invalid") from error
        if not isinstance(text, str) or not text.strip():
            raise RawRuntimeError("RAW llama.cpp returned empty text")
        return text


class RawLlamaRuntime:
    """Bounded lifecycle for the exact llama.cpp child on one loopback port."""

    def __init__(
        self,
        artifact: RawArtifactValidator | None = None,
        *,
        runtime_root: Path = RAW_RUNTIME_ROOT,
        llama_server: Path = RAW_LLAMA_SERVER,
        host: str = RAW_HOST,
        port: int = RAW_PORT,
        provider: RawLlamaProvider | None = None,
        snapshot: Callable[[int], ProcessIdentity | None] = process_snapshot,
        listeners: Callable[[int], tuple[int, ...]] = listener_pids,
        process_factory: Callable[..., Any] = subprocess.Popen,
        signaler: Callable[[int, int], None] = os.kill,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        memory_check: Callable[[], bool] | None = None,
    ):
        if host != RAW_HOST or int(port) != RAW_PORT:
            raise ValueError("RAW runtime may bind only the fixed loopback endpoint")
        self.artifact = artifact or RawArtifactValidator()
        self.runtime_root = Path(runtime_root)
        self.llama_server = Path(llama_server)
        self.host = host
        self.port = int(port)
        self.provider = provider or RawLlamaProvider(host=host, port=port)
        self.snapshot = snapshot
        self.listeners = listeners
        self.process_factory = process_factory
        self.signaler = signaler
        self.sleeper = sleeper
        self.monotonic = monotonic
        self.memory_check = memory_check or (lambda: MemoryPreflight().check(30).allowed)
        self.identity_path = self.runtime_root / "raw-llama.identity.json"
        self._child: Any | None = None

    def command(self) -> tuple[str, ...]:
        return (
            str(self.llama_server),
            "--model", str(self.artifact.model_path),
            "--host", self.host,
            "--port", str(self.port),
            "--ctx-size", str(RAW_CONTEXT_TOKENS),
            "--parallel", "1",
            "--n-gpu-layers", "999",
        )

    def _safe_env(self) -> dict[str, str]:
        home = self.runtime_root / "home"
        temporary = self.runtime_root / "tmp"
        for path in (self.runtime_root, home, temporary):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        return {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "LANG": "C",
            "LC_ALL": "C",
        }

    def _validate_executable(self) -> None:
        if not self.llama_server.is_absolute():
            raise RawModelUnavailable("llama-server path must be absolute")
        try:
            metadata = self.llama_server.lstat()
            resolved = self.llama_server.resolve(strict=True)
            resolved_metadata = resolved.stat()
        except OSError as error:
            raise RawModelUnavailable("llama-server is not installed at the pinned path") from error
        trusted_homebrew_link = (
            stat.S_ISLNK(metadata.st_mode)
            and self.llama_server == RAW_LLAMA_SERVER
            and resolved.is_relative_to(Path("/opt/homebrew/Cellar/llama.cpp"))
        )
        if (stat.S_ISLNK(metadata.st_mode) and not trusted_homebrew_link) or not stat.S_ISREG(resolved_metadata.st_mode) or not os.access(resolved, os.X_OK):
            raise RawModelUnavailable("llama-server pinned executable is unsafe or unavailable")

    def _runtime_availability(self) -> str:
        try:
            self._validate_executable()
            return "AVAILABLE"
        except RawModelUnavailable:
            return "NOT_INSTALLED_OR_UNSAFE"

    def _endpoint_healthy(self) -> bool:
        try:
            result = self.provider.health()
            return result.get("status") in {"ok", "OK", "healthy", "HEALTHY"}
        except Exception:
            return False

    def status(self) -> RawRuntimeStatus:
        artifact = self.artifact.inspect()
        runtime = self._runtime_availability()
        if artifact.state is not RawRuntimeState.READY:
            return RawRuntimeStatus(
                artifact.state, artifact.path, artifact.integrity, artifact.size_bytes,
                artifact.incomplete_files, runtime, f"http://{self.host}:{self.port}",
                None, "NOT_CHECKED", (),
            )
        process_state, pid = identity_status(self.identity_path, snapshot=self.snapshot)
        try:
            pids = tuple(self.listeners(self.port))
        except Exception:
            return RawRuntimeStatus(
                RawRuntimeState.UNHEALTHY, artifact.path, artifact.integrity, artifact.size_bytes,
                artifact.incomplete_files, runtime, f"http://{self.host}:{self.port}",
                pid, process_state, (),
            )
        if process_state == "MATCH" and pids == (pid,) and self._endpoint_healthy():
            state = RawRuntimeState.RUNNING
        elif process_state == "MATCH" or pids or process_state == "INVALID":
            state = RawRuntimeState.UNHEALTHY
        else:
            state = RawRuntimeState.READY
        return RawRuntimeStatus(
            state, artifact.path, artifact.integrity, artifact.size_bytes,
            artifact.incomplete_files, runtime, f"http://{self.host}:{self.port}",
            pid, process_state, pids,
        )

    @staticmethod
    def _poll(process: Any) -> int | None:
        return process.poll()

    def _reap_spawned(self, process: Any) -> bool:
        if self._poll(process) is not None:
            process.wait(timeout=0)
            return True
        process.terminate()
        try:
            process.wait(timeout=10)
            return True
        except subprocess.TimeoutExpired:
            if self._poll(process) is None:
                process.kill()
            process.wait(timeout=5)
            return True

    def _remove_spawn_identity(self, identity: ProcessIdentity | None) -> None:
        if identity is not None and read_identity(self.identity_path) == identity:
            self.identity_path.unlink(missing_ok=True)

    def start(self, *, timeout_seconds: int = RAW_START_TIMEOUT_SECONDS) -> RawRuntimeStatus:
        inspection = self.artifact.inspect()
        if inspection.state is not RawRuntimeState.READY:
            raise RawModelUnavailable(f"RAW model is {inspection.state.value}: {inspection.integrity}")
        self._validate_executable()
        current_state, current_pid = identity_status(self.identity_path, snapshot=self.snapshot)
        current_listeners = tuple(self.listeners(self.port))
        if current_state == "MATCH" and current_listeners == (current_pid,):
            if self._endpoint_healthy():
                return self.status()
            raise RawProcessConflict("owned RAW runtime exists but is unhealthy; duplicate start denied")
        if current_state in {"MATCH", "INVALID"} or current_listeners:
            raise RawProcessConflict("RAW runtime ownership is ambiguous; no process was controlled")
        if tuple(self.listeners(8000)) or tuple(self.listeners(8001)):
            raise RawProcessConflict("another heavy model listener is resident; RAW start denied")
        if not self.memory_check():
            raise RawModelUnavailable("RAW memory preflight denied start")
        self.runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.runtime_root, 0o700)
        log_path = self.runtime_root / "raw-llama.log"
        spawned_identity: ProcessIdentity | None = None
        with log_path.open("ab", buffering=0) as log:
            process = self.process_factory(
                self.command(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=self.runtime_root,
                env=self._safe_env(),
                shell=False,
                start_new_session=True,
            )
        self._child = process
        try:
            for _ in range(20):
                spawned_identity = self.snapshot(process.pid)
                if spawned_identity is not None:
                    break
                if self._poll(process) is not None:
                    break
                self.sleeper(0.05)
            if spawned_identity is None or not expected_spawn_identity(spawned_identity, self.command()):
                raise RawProcessConflict("spawned llama-server identity did not match exact argv")
            write_identity(self.identity_path, spawned_identity)
            deadline = self.monotonic() + max(1, min(int(timeout_seconds), RAW_START_TIMEOUT_SECONDS))
            while self.monotonic() < deadline:
                if self._poll(process) is not None:
                    raise RawRuntimeError("llama-server exited before health became ready")
                if tuple(self.listeners(self.port)) == (process.pid,) and self._endpoint_healthy():
                    return self.status()
                self.sleeper(0.25)
            raise TimeoutError("RAW llama-server bounded start timeout")
        except Exception:
            cleanup_confirmed = False
            try:
                cleanup_confirmed = self._reap_spawned(process)
            finally:
                if cleanup_confirmed:
                    self._remove_spawn_identity(spawned_identity)
            raise

    def stop(self, *, timeout_seconds: int = 20) -> str:
        saved = read_identity(self.identity_path)
        pids = tuple(self.listeners(self.port))
        if saved is None:
            if pids:
                raise RawProcessConflict("unknown RAW port listener; no process was controlled")
            return "ALREADY_STOPPED"
        current = self.snapshot(saved.pid)
        if current != saved or (pids and pids != (saved.pid,)):
            raise RawProcessConflict("RAW process identity mismatch; no process was controlled")
        if self._child is not None and getattr(self._child, "pid", None) == saved.pid:
            self._reap_spawned(self._child)
        else:
            self.signaler(saved.pid, signal.SIGTERM)
            deadline = self.monotonic() + max(1, min(int(timeout_seconds), 60))
            while self.monotonic() < deadline:
                if self.snapshot(saved.pid) != saved and not tuple(self.listeners(self.port)):
                    self.identity_path.unlink(missing_ok=True)
                    return "STOPPED"
                self.sleeper(0.1)
            if self.snapshot(saved.pid) == saved:
                self.signaler(saved.pid, signal.SIGKILL)
            else:
                raise RawProcessConflict("RAW PID identity changed; kill refused")
            for _ in range(50):
                if self.snapshot(saved.pid) != saved and not tuple(self.listeners(self.port)):
                    self.identity_path.unlink(missing_ok=True)
                    return "STOPPED"
                self.sleeper(0.1)
            raise RawRuntimeError("RAW process could not be confirmed stopped")
        self.identity_path.unlink(missing_ok=True)
        return "STOPPED"


DENIED_RAW_HOST_CAPABILITIES = frozenset({
    "arbitrary_shell", "credential_access", "host_file_access", "keychain", "ssh_keys",
    "wallet_secrets", "package_install", "downloads", "service_control", "process_kill",
    "git_write", "authenticated_egress", "public_listener", "identity_change", "tool_grant",
})


class OwnerRawService:
    """Identity-bound service; inference returns text and cannot dispatch tools."""

    def __init__(
        self,
        runtime: RawLlamaRuntime | None = None,
        provider: RawLlamaProvider | None = None,
        router: ProviderRouter | None = None,
    ):
        self.runtime = runtime or RawLlamaRuntime()
        self.provider = provider or self.runtime.provider
        self.router = router or default_provider_router()

    def _authorize(self, identity: IdentityContext | None) -> None:
        if not isinstance(identity, IdentityContext):
            raise AuthorizationDenied("explicit Owner identity is required")
        if identity.role is not Role.OWNER or identity.scope != "owner_private" or not identity.internal_user_id.startswith("owner:"):
            raise AuthorizationDenied("RAW model is Owner-only")
        self.router.route(ProviderRequest(
            capability=Capability.RESEARCH,
            privacy=PrivacyMode.PRIVATE,
            purpose=InvocationPurpose.OWNER_RAW_RESEARCH,
            explicit_provider=RAW_PROVIDER_ID,
            owner_authorized=True,
        ))

    @staticmethod
    def authorize_model_host_action(_action: str) -> None:
        raise PermissionError("RAW model has no host capability authority")

    def status(self, identity: IdentityContext | None) -> RawRuntimeStatus:
        self._authorize(identity)
        return self.runtime.status()

    def health(self, identity: IdentityContext | None) -> dict[str, Any]:
        self._authorize(identity)
        status = self.runtime.status()
        if status.state is not RawRuntimeState.RUNNING:
            raise RawModelUnavailable(f"RAW runtime is {status.state.value}")
        return self.provider.health()

    def start(self, identity: IdentityContext | None) -> RawRuntimeStatus:
        self._authorize(identity)
        return self.runtime.start()

    def stop(self, identity: IdentityContext | None) -> str:
        self._authorize(identity)
        return self.runtime.stop()

    def generate(self, identity: IdentityContext | None, prompt: str, *, max_output_tokens: int = 512) -> str:
        self._authorize(identity)
        if self.runtime.status().state is not RawRuntimeState.RUNNING:
            raise RawModelUnavailable("RAW runtime is not running")
        return self.provider.generate(prompt, max_output_tokens=max_output_tokens)


def local_owner_identity(ai_root: Path = Path("/Users/jerson/AI")) -> IdentityContext:
    """Bind CLI authority to the local filesystem owner's effective UID."""
    root = Path(ai_root).resolve(strict=True)
    if os.geteuid() != root.stat().st_uid:
        raise AuthorizationDenied("local CLI user does not own the AI platform root")
    uid = str(os.geteuid())
    return IdentityContext(uid, f"owner:local-euid:{uid}", Role.OWNER, "owner_private")
