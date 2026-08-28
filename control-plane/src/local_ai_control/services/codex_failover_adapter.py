"""Codex client adapter for one durable Local-Qwen failover job.

This module prepares an isolated local-provider profile and routes an existing
Supervisor stage.  It never mutates the Owner's normal ``~/.codex`` tree and it
does not claim that Codex Desktop can hot-swap an already-running chat thread.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile

from .codex_qwen_workspace import DEFAULT_BRIDGE_URL, prepare_codex_home
from .provider_failover import ProviderIdentity, ProviderState
from .supervisor_contracts import StageResult, StageResultStatus, ensure_private_directory


DEFAULT_FAILOVER_RUNTIME = Path("/Users/jerson/AI/runtime/codex-failover")


@dataclass(frozen=True)
class CodexLocalLaunchPlan:
    job_id: str
    effective_provider: str
    workspace_path: str
    branch: str
    codex_home: str
    environment_overrides: tuple[tuple[str, str], ...]
    cli_argv: tuple[str, ...]
    desktop_argv: tuple[str, ...]
    same_thread_hot_swap_supported: bool
    desktop_mode: str
    status_path: str

    def safe_payload(self) -> dict:
        return {
            "schema_version": "0.1",
            "job_id": self.job_id,
            "effective_provider": self.effective_provider,
            "workspace_path": self.workspace_path,
            "branch": self.branch,
            "codex_home": self.codex_home,
            "environment_overrides": dict(self.environment_overrides),
            "cli_argv": list(self.cli_argv),
            "desktop_argv": list(self.desktop_argv),
            "same_thread_hot_swap_supported": self.same_thread_hot_swap_supported,
            "desktop_mode": self.desktop_mode,
        }


class CodexDesktopLocalAdapter:
    """Prepare, but never auto-launch, an isolated local Codex client profile."""

    def __init__(self, repository, *, runtime_root: Path = DEFAULT_FAILOVER_RUNTIME,
                 codex_executable: str = "codex", bridge_url: str = DEFAULT_BRIDGE_URL,
                 home_preparer=prepare_codex_home):
        self.repository = repository
        self.runtime_root = Path(runtime_root)
        self.codex_executable = str(codex_executable)
        self.bridge_url = bridge_url
        self.home_preparer = home_preparer

    def prepare(self, job_id: str) -> CodexLocalLaunchPlan:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,46}", job_id):
            raise ValueError("invalid durable job id")
        job = self.repository.get_job(job_id)
        state = self.repository.provider_state(job_id)
        if state["current_provider"] != ProviderIdentity.LOCAL_QWEN.value or state["state"] not in {
            ProviderState.LOCAL_QWEN.value, ProviderState.CONTINUE_SAME_JOB.value,
        }:
            raise RuntimeError("LOCAL_PROVIDER_NOT_ACTIVE_FOR_JOB")
        if str(Path(job.project_scope).resolve()) != state["workspace_path"]:
            raise RuntimeError("LOCAL_ADAPTER_WORKSPACE_BINDING_MISMATCH")
        evidence, home = self.home_preparer(
            job.project_scope, bridge_url=self.bridge_url,
            runtime_root=self.runtime_root / "codex-homes",
        )
        if str(evidence.root) != state["workspace_path"] or evidence.branch != state["branch"]:
            raise RuntimeError("LOCAL_ADAPTER_BRANCH_BINDING_MISMATCH")

        status_root = ensure_private_directory(self.runtime_root / "status")
        status_path = status_root / f"{job_id}.json"
        plan = CodexLocalLaunchPlan(
            job_id, ProviderIdentity.LOCAL_QWEN.value, state["workspace_path"], state["branch"],
            str(home),
            (("CODEX_HOME", str(home)),),
            (self.codex_executable, "-C", state["workspace_path"]),
            (self.codex_executable, "app", state["workspace_path"]),
            False,
            "BEST_EFFORT_NEW_LOCAL_SESSION",
            str(status_path),
        )
        descriptor, temporary = tempfile.mkstemp(prefix=".status-", dir=status_root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(plan.safe_payload(), stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, status_path)
            os.chmod(status_path, 0o600)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return plan


class ProviderAwareCodexStageRunner:
    """Route one existing StageContext without changing its logical job identity."""

    def __init__(self, repository, *, cloud_stage_runner, local_stage_runner):
        self.repository = repository
        self.cloud_stage_runner = cloud_stage_runner
        self.local_stage_runner = local_stage_runner
        self.active_runner = None

    @property
    def cancellation_supported(self) -> bool:
        return bool(self.active_runner and getattr(self.active_runner, "cancellation_supported", False))

    def cancel(self, execution_id=None, reason=None) -> bool:
        if self.active_runner is None or not hasattr(self.active_runner, "cancel"):
            return False
        return bool(self.active_runner.cancel(execution_id=execution_id, reason=reason))

    def run(self, context) -> StageResult:
        if context.repository is not self.repository:
            return StageResult(
                StageResultStatus.BLOCKED, "Provider adapter repository binding mismatch",
                error="PROVIDER_ADAPTER_REPOSITORY_MISMATCH",
            )
        state = self.repository.provider_state(context.job.job_id)
        if str(Path(context.job.project_scope).resolve()) != state["workspace_path"]:
            return StageResult(
                StageResultStatus.BLOCKED, "Provider adapter workspace binding mismatch",
                error="PROVIDER_ADAPTER_WORKSPACE_MISMATCH",
            )
        provider = state["current_provider"]
        provider_state = state["state"]
        if provider_state in {ProviderState.BLOCKED.value, ProviderState.HANDOFF_PENDING.value,
                              ProviderState.LOCAL_PREFLIGHT.value}:
            return StageResult(
                StageResultStatus.BLOCKED, "Provider handoff is not executable",
                error=f"PROVIDER_HANDOFF_{provider_state}",
            )
        if provider == ProviderIdentity.LOCAL_QWEN.value:
            self.active_runner = self.local_stage_runner
        elif provider == ProviderIdentity.OPENAI_CODEX.value:
            self.active_runner = self.cloud_stage_runner
        else:
            return StageResult(
                StageResultStatus.BLOCKED, "Unknown execution provider",
                error="UNKNOWN_EXECUTION_PROVIDER",
            )
        return self.active_runner.run(context)
