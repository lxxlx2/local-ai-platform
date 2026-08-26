from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import time
from typing import Callable


class CodexQuotaProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexQuotaSnapshot:
    primary_used_percent: int
    primary_resets_at: int
    secondary_used_percent: int
    secondary_resets_at: int
    plan_type: str

    def metrics(self, prefix: str) -> dict[str, int | str]:
        return {
            f"{prefix}_primary_used_percent": self.primary_used_percent,
            f"{prefix}_primary_resets_at": self.primary_resets_at,
            f"{prefix}_secondary_used_percent": self.secondary_used_percent,
            f"{prefix}_secondary_resets_at": self.secondary_resets_at,
            f"{prefix}_plan_type": self.plan_type,
        }


def quota_increases(before: CodexQuotaSnapshot, after: CodexQuotaSnapshot) -> tuple[str, ...]:
    changed: list[str] = []
    if before.primary_resets_at == after.primary_resets_at:
        if after.primary_used_percent > before.primary_used_percent:
            changed.append("primary")
    elif after.primary_used_percent > 0:
        changed.append("primary_window_changed_with_usage")

    if before.secondary_resets_at == after.secondary_resets_at:
        if after.secondary_used_percent > before.secondary_used_percent:
            changed.append("secondary")
    elif after.secondary_used_percent > 0:
        changed.append("secondary_window_changed_with_usage")
    return tuple(changed)


class CodexQuotaProbe:
    """Read Codex account rate limits without issuing a model turn.

    The probe always removes CODEX_HOME so the telemetry request uses the normal
    account context instead of a Local-Qwen isolated CODEX_HOME. It invokes only
    app-server account/rateLimits/read and never submits a prompt.
    """

    def __init__(self, codex: str | Path | None = None, *, timeout_seconds: float = 20.0):
        if codex is None:
            preferred = Path.home() / ".local/bin/codex"
            resolved = str(preferred) if preferred.exists() else shutil.which("codex")
        else:
            resolved = str(Path(codex).expanduser())
        if not resolved:
            raise CodexQuotaProbeError("Codex executable is unavailable")
        self.codex = resolved
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _send(process: subprocess.Popen, payload: dict) -> None:
        if process.stdin is None:
            raise CodexQuotaProbeError("Codex app-server stdin unavailable")
        process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _wait_for(self, process: subprocess.Popen, request_id: int) -> dict:
        if process.stdout is None:
            raise CodexQuotaProbeError("Codex app-server stdout unavailable")
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [process.stdout],
                [],
                [],
                min(1.0, max(0.0, deadline - time.monotonic())),
            )
            if not ready:
                if process.poll() is not None:
                    raise CodexQuotaProbeError("Codex app-server exited before quota response")
                continue
            line = process.stdout.readline()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == request_id:
                if "error" in payload:
                    raise CodexQuotaProbeError("Codex rate-limit request returned an error")
                return payload
        raise CodexQuotaProbeError("Codex rate-limit request timed out")

    def snapshot(self) -> CodexQuotaSnapshot:
        environment = os.environ.copy()
        environment.pop("CODEX_HOME", None)
        process = subprocess.Popen(
            [self.codex, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=environment,
            shell=False,
        )
        try:
            self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "local-qwen-quota-guard", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                },
            )
            self._wait_for(process, 1)
            self._send(process, {"jsonrpc": "2.0", "method": "initialized"})
            self._send(process, {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"})
            response = self._wait_for(process, 2)
            limits = (response.get("result") or {}).get("rateLimits") or {}
            primary = limits.get("primary") or {}
            secondary = limits.get("secondary") or {}
            try:
                return CodexQuotaSnapshot(
                    int(primary["usedPercent"]),
                    int(primary["resetsAt"]),
                    int(secondary["usedPercent"]),
                    int(secondary["resetsAt"]),
                    str(limits.get("planType") or "unknown"),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise CodexQuotaProbeError("Codex rate-limit response schema is incomplete") from error
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


class CodexQuotaGuard:
    """Bounded, fail-closed rate-limit telemetry around a local-only task."""

    def __init__(
        self,
        snapshotter: Callable[[], CodexQuotaSnapshot] | None = None,
        *,
        attempts: int = 3,
        retry_delay_seconds: float = 0.75,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not 1 <= int(attempts) <= 5:
            raise ValueError("quota probe attempts outside safe bound")
        if not 0 <= float(retry_delay_seconds) <= 5:
            raise ValueError("quota probe retry delay outside safe bound")
        self.snapshotter = snapshotter or CodexQuotaProbe().snapshot
        self.attempts = int(attempts)
        self.retry_delay_seconds = float(retry_delay_seconds)
        self.sleeper = sleeper

    def _snapshot_with_retry(self, phase: str) -> CodexQuotaSnapshot:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                return self.snapshotter()
            except (CodexQuotaProbeError, OSError, subprocess.SubprocessError) as error:
                last_error = error
                if attempt < self.attempts and self.retry_delay_seconds:
                    self.sleeper(self.retry_delay_seconds)
        if last_error is None:
            raise CodexQuotaProbeError(f"Codex quota {phase} failed without an error")
        detail = str(last_error).replace("\n", " ")[:240]
        raise CodexQuotaProbeError(
            f"Codex quota {phase} failed after {self.attempts} attempts: "
            f"{type(last_error).__name__}: {detail}"
        ) from last_error

    def before(self) -> CodexQuotaSnapshot:
        return self._snapshot_with_retry("precheck")

    def after(self, before: CodexQuotaSnapshot) -> tuple[CodexQuotaSnapshot, tuple[str, ...]]:
        after = self._snapshot_with_retry("postcheck")
        return after, quota_increases(before, after)
