from __future__ import annotations

import shutil
import subprocess
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
    def run_task(self, spec: CodexTaskSpec) -> StageResult: ...


class RealCodexRunner:
    """Execution boundary remains disabled until a later authorized review."""

    def __init__(self, capability: CodexCapability | None = None):
        self.capability = capability or CodexCapabilityProbe().probe()

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
        return self.task_runner.run_task(spec)
