from __future__ import annotations

import shutil
import subprocess
import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from .supervisor_contracts import (
    CodexTaskSpec, StageContext, StageResult, StageResultStatus, WorkflowStage, _bounded,
)


@dataclass(frozen=True)
class CodexCapability:
    status: str
    executable: str | None
    version: str | None
    noninteractive_surface: bool
    app_server_surface: bool
    existing_auth_reuse: str
    reason: str


class CodexCapabilityProbe:
    """Version/help-only probe. It never reads auth files or starts a task."""

    def probe(self) -> CodexCapability:
        executable = shutil.which("codex")
        if not executable:
            return CodexCapability("NOT_CONFIGURED", None, None, False, False, "NOT_CONFIRMED", "codex executable not found")
        try:
            version = subprocess.run([executable, "--version"], capture_output=True, text=True, shell=False,
                                     timeout=5, check=False)
            help_result = subprocess.run([executable, "--help"], capture_output=True, text=True, shell=False,
                                         timeout=5, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            return CodexCapability("PARTIAL", executable, None, False, False, "NOT_CONFIRMED", type(error).__name__)
        help_text = (help_result.stdout + "\n" + help_result.stderr).lower()
        app_server = "app-server" in help_text or "app server" in help_text
        noninteractive = any(marker in help_text for marker in ("exec", "json", "non-interactive", "app-server"))
        status = "AVAILABLE" if version.returncode == 0 and noninteractive else "PARTIAL"
        auth_reuse = "PARTIAL" if status == "AVAILABLE" else "NOT_CONFIRMED"
        return CodexCapability(
            status, executable, _bounded(version.stdout.strip() or version.stderr.strip(), 200),
            noninteractive, app_server, auth_reuse,
            "Version/help surface only; auth and task execution were not probed",
        )


class CodexTaskRunner(Protocol):
    cancellation_supported: bool
    def run_task(self, spec: CodexTaskSpec) -> StageResult: ...
    def cancel(self, execution_id: str | None = None, reason: str | None = None) -> bool: ...


class RealCodexRunner:
    """Execution boundary remains disabled until a later authorized review."""

    def __init__(self, capability: CodexCapability | None = None):
        self.capability = capability or CodexCapabilityProbe().probe()
        self.cancellation_supported = False

    def cancel(self, execution_id: str | None = None, reason: str | None = None) -> bool:
        return False

    def run_task(self, spec: CodexTaskSpec) -> StageResult:
        spec.validate()
        return StageResult(
            StageResultStatus.BLOCKED,
            "Real Codex task execution is disabled pending independent review",
            error="REAL_CODEX_EXECUTION_REVIEW_PENDING",
            metrics={"capability": self.capability.status, "app_server": self.capability.app_server_surface},
        )


class PersistedCodexStageRunner:
    """StageRunner that reconstructs its task only from durable job/work-unit state."""

    def __init__(self, task_runner: CodexTaskRunner | None = None):
        self.task_runner = task_runner or RealCodexRunner()
        self.execution_id: str | None = None
        self.cancellation_result: str | None = None

    @property
    def cancellation_supported(self) -> bool:
        return bool(getattr(self.task_runner, "cancellation_supported", False))

    def cancel(self, execution_id: str | None = None, reason: str | None = None) -> bool:
        if not self.cancellation_supported:
            self.cancellation_result = "UNSUPPORTED"
            return False
        active = self.execution_id
        if execution_id is not None and active is not None and execution_id != active:
            self.cancellation_result = "EXECUTION_ID_MISMATCH"
            return False
        if active is not None and not re.fullmatch(r"[a-f0-9-]{36}", active):
            self.cancellation_result = "UNSAFE_EXECUTION_ID"
            return False
        try:
            result = bool(self.task_runner.cancel(execution_id=active, reason=reason))
        except Exception:
            self.cancellation_result = "CANCEL_FAILED"
            return False
        self.cancellation_result = "CANCELED" if result else "CANCEL_FAILED"
        return result

    def run(self, context: StageContext) -> StageResult:
        if context.stage not in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}:
            return StageResult(StageResultStatus.BLOCKED, "Codex stage runner scope denied", error="CODEX_STAGE_SCOPE")
        if context.stage is WorkflowStage.REVISION and not context.current_review_findings():
            return StageResult(StageResultStatus.BLOCKED, "Revision findings unavailable", error="REVISION_FINDINGS_NOT_AVAILABLE")
        try:
            spec = context.repository.reconstruct_codex_task(
                context.job.job_id, context.job.owner_id, context.stage,
                context.job.review_round if context.stage is WorkflowStage.REVISION else 0,
            )
        except (KeyError, ValueError, PermissionError) as error:
            return StageResult(StageResultStatus.BLOCKED, "Durable work unit unavailable or invalid", error=type(error).__name__)
        self.execution_id = str(uuid.uuid4())
        try:
            return self.task_runner.run_task(spec)
        finally:
            self.execution_id = None
