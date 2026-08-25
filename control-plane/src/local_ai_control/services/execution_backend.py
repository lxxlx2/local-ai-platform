from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ToolIntent:
    tool: str
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    allow_network: bool = False


@dataclass(frozen=True)
class ToolResult:
    status: str
    returncode: int | None
    duration_ms: int
    stdout_summary: str = ""
    stderr_summary: str = ""


@dataclass(frozen=True)
class ExecutionPolicy:
    workspace_root: Path
    allowed_tools: frozenset[str]
    allowed_write_roots: tuple[Path, ...]
    network_allowed: bool = False
    max_timeout_seconds: int = 900

    def validate(self, intent: ToolIntent) -> None:
        root = self.workspace_root.resolve(strict=True)
        cwd = intent.cwd.resolve(strict=True)
        if root != cwd and root not in cwd.parents:
            raise PermissionError("execution cwd escapes workspace")
        if intent.tool not in self.allowed_tools:
            raise PermissionError("tool is not allowlisted")
        if intent.timeout_seconds <= 0 or intent.timeout_seconds > self.max_timeout_seconds:
            raise PermissionError("execution timeout exceeds policy")
        if intent.allow_network and not self.network_allowed:
            raise PermissionError("network access is denied")
        for write_root in self.allowed_write_roots:
            resolved = write_root.resolve(strict=True)
            if resolved != root and root not in resolved.parents:
                raise PermissionError("write root escapes workspace")


class ExecutionBackend(Protocol):
    """Host-owned execution boundary used by local models.

    Model output can request structured ToolIntent values, but only this backend
    and its host policy may execute them. Retrieved documents/web pages never
    receive execution authority.
    """

    def execute(self, intent: ToolIntent, policy: ExecutionPolicy) -> ToolResult: ...


class LocalToolExecutor:
    """Placeholder direct executor boundary for the next implementation slice.

    The current qualified local Qwen -> Codex-CLI bridge remains usable while
    this direct backend is implemented. This class intentionally fails closed
    until a concrete process adapter with bounded output and cancellation is
    attached.
    """

    def execute(self, intent: ToolIntent, policy: ExecutionPolicy) -> ToolResult:
        policy.validate(intent)
        raise RuntimeError("LOCAL_TOOL_EXECUTOR_NOT_IMPLEMENTED")


def normalize_argv(values: Sequence[str]) -> tuple[str, ...]:
    result = tuple(values)
    if not result or any(not isinstance(value, str) or not value for value in result):
        raise ValueError("argv must contain non-empty strings")
    return result
