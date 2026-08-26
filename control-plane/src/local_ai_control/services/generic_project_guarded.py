from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from .codex_quota_guard import CodexQuotaGuard, CodexQuotaProbeError
from .generic_project_policy import TestProfile
from .supervisor_codex import PersistedCodexStageRunner
from .supervisor_contracts import StageResult, StageResultStatus, WorkflowStage, ensure_private_directory
from .supervisor_generic_project import GenericProjectQwenCodexRunner, generic_project_runners
from .supervisor_local_qwen import LocalProducerExecutionUncertain


DEFAULT_EXECUTION_LOG_ROOT = Path("/Users/jerson/AI/runtime/generic-projects/execution-logs")


class ManagedLocalCodexCommandRunner:
    """Run Codex in its own process group and prove it is gone on timeout."""

    def __init__(self, log_root: Path = DEFAULT_EXECUTION_LOG_ROOT):
        self.log_root = Path(log_root)

    @staticmethod
    def _terminate_group(process: subprocess.Popen) -> bool:
        if process.poll() is not None:
            return True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return process.poll() is not None
        try:
            process.wait(timeout=8)
            return True
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None:
            return True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return process.poll() is not None
        try:
            process.wait(timeout=5)
            return True
        except subprocess.TimeoutExpired:
            return False

    def __call__(self, command, **kwargs):
        if kwargs.get("shell", False):
            raise ValueError("managed Local Codex runner forbids shell=True")
        timeout = float(kwargs.get("timeout", 0))
        if timeout <= 0:
            raise ValueError("managed Local Codex runner requires a positive timeout")
        cwd = kwargs.get("cwd")
        env = kwargs.get("env")
        stdin = kwargs.get("stdin", subprocess.DEVNULL)
        check = bool(kwargs.get("check", False))

        root = ensure_private_directory(self.log_root)
        log_path = root / f"codex-{int(time.time())}-{uuid.uuid4().hex[:12]}.log"
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=stdin,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=True,
            )
            try:
                return_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if not self._terminate_group(process):
                    raise LocalProducerExecutionUncertain(
                        "GENERIC_LOCAL_QWEN_PROCESS_GROUP_COULD_NOT_BE_REAPED"
                    )
                return_code = 124

        completed = subprocess.CompletedProcess(command, return_code)
        if check and return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)
        return completed


class GuardedGenericProjectQwenCodexRunner(GenericProjectQwenCodexRunner):
    """Generic Local-Qwen runner with quota leakage detection and bounded process cleanup."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        health_probe=None,
        quota_guard: CodexQuotaGuard | None = None,
        command_runner=None,
    ):
        super().__init__(
            enabled=enabled,
            health_probe=health_probe,
            command_runner=command_runner or ManagedLocalCodexCommandRunner(),
        )
        self.quota_guard = quota_guard or CodexQuotaGuard()

    def run_task(self, spec, execution_id: str) -> StageResult:
        try:
            before = self.quota_guard.before()
        except (CodexQuotaProbeError, OSError, subprocess.SubprocessError) as error:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Codex quota telemetry unavailable before local-only execution",
                error="CODEX_QUOTA_PRECHECK_UNAVAILABLE",
                metrics={"category": type(error).__name__},
            )

        try:
            result = super().run_task(spec, execution_id)
        except LocalProducerExecutionUncertain as error:
            try:
                _after, changes = self.quota_guard.after(before)
            except Exception:
                raise
            if changes:
                raise LocalProducerExecutionUncertain("CODEX_QUOTA_LEAK_DETECTED") from error
            raise

        try:
            after, changes = self.quota_guard.after(before)
        except (CodexQuotaProbeError, OSError, subprocess.SubprocessError) as error:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Codex quota telemetry unavailable after local-only execution",
                error="CODEX_QUOTA_POSTCHECK_UNAVAILABLE",
                metrics=(result.metrics or {}) | before.metrics("quota_before") | {
                    "category": type(error).__name__,
                },
            )

        quota_metrics = (
            before.metrics("quota_before")
            | after.metrics("quota_after")
            | {"codex_quota_guard": "PASS" if not changes else "LEAK_DETECTED"}
        )
        if changes:
            return StageResult(
                StageResultStatus.BLOCKED,
                "OpenAI Codex quota changed during a local-only Qwen task",
                error="CODEX_QUOTA_LEAK_DETECTED",
                metrics=(result.metrics or {}) | quota_metrics | {"changed_scopes": list(changes)},
            )
        result.metrics.update(quota_metrics)
        return result


def guarded_generic_project_runners(
    repo_root: Path,
    *,
    enabled: bool = False,
    test_profile: TestProfile = TestProfile.NONE,
    gemini_gateway=None,
    quota_guard: CodexQuotaGuard | None = None,
):
    runners = generic_project_runners(
        repo_root,
        enabled=enabled,
        test_profile=test_profile,
        gemini_gateway=gemini_gateway,
    )
    guard = quota_guard or CodexQuotaGuard()
    runners[WorkflowStage.PRODUCER] = PersistedCodexStageRunner(
        GuardedGenericProjectQwenCodexRunner(enabled=enabled, quota_guard=guard)
    )
    runners[WorkflowStage.REVISION] = PersistedCodexStageRunner(
        GuardedGenericProjectQwenCodexRunner(enabled=enabled, quota_guard=guard)
    )
    return runners
