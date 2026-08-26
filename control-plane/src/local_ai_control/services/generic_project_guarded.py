from __future__ import annotations

import subprocess
from pathlib import Path

from .codex_quota_guard import CodexQuotaGuard, CodexQuotaProbeError
from .direct_local_qwen_verified import VerifiedDirectGenericProjectQwenRunner
from .generic_project_policy import TestProfile
from .supervisor_codex import PersistedCodexStageRunner
from .supervisor_contracts import StageResult, StageResultStatus, WorkflowStage
from .supervisor_generic_project import generic_project_runners


class GuardedDirectGenericProjectQwenRunner(VerifiedDirectGenericProjectQwenRunner):
    """Verified Direct Local-Qwen executor with OpenAI Codex quota leak detection.

    The implementation path never starts Codex CLI. The quota probe only reads
    account/rateLimits/read before and after the local task so any unrelated or
    accidental quota movement fails closed. A task also cannot PASS until the
    verified direct-agent layer proves a non-empty candidate diff, a post-write
    diff inspection, and post-write fixed tests when a test profile is selected.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        provider=None,
        test_profile: TestProfile = TestProfile.NONE,
        quota_guard: CodexQuotaGuard | None = None,
    ):
        super().__init__(enabled=enabled, provider=provider, test_profile=test_profile)
        self.quota_guard = quota_guard or CodexQuotaGuard()

    @staticmethod
    def _probe_error_metrics(error: Exception) -> dict[str, str | bool]:
        return {
            "category": type(error).__name__,
            "quota_probe_error": str(error).replace("\n", " ")[:400],
            "codex_cli_invoked": False,
        }

    def run_task(self, spec, execution_id: str) -> StageResult:
        try:
            before = self.quota_guard.before()
        except (CodexQuotaProbeError, OSError, subprocess.SubprocessError) as error:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Codex quota telemetry unavailable before local-only execution",
                error="CODEX_QUOTA_PRECHECK_UNAVAILABLE",
                metrics=self._probe_error_metrics(error),
            )

        result = super().run_task(spec, execution_id)

        try:
            after, changes = self.quota_guard.after(before)
        except (CodexQuotaProbeError, OSError, subprocess.SubprocessError) as error:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Codex quota telemetry unavailable after local-only execution",
                error="CODEX_QUOTA_POSTCHECK_UNAVAILABLE",
                metrics=(result.metrics or {})
                | before.metrics("quota_before")
                | self._probe_error_metrics(error),
            )

        quota_metrics = (
            before.metrics("quota_before")
            | after.metrics("quota_after")
            | {
                "codex_quota_guard": "PASS" if not changes else "LEAK_DETECTED",
                "codex_cli_invoked": False,
                "local_executor": "direct-qwen-tools-verified",
            }
        )
        if changes:
            return StageResult(
                StageResultStatus.BLOCKED,
                "OpenAI Codex quota changed during a direct local-only Qwen task",
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
        enabled=False,
        test_profile=test_profile,
        gemini_gateway=gemini_gateway,
    )
    guard = quota_guard or CodexQuotaGuard()
    runners[WorkflowStage.PRODUCER] = PersistedCodexStageRunner(
        GuardedDirectGenericProjectQwenRunner(
            enabled=enabled,
            test_profile=test_profile,
            quota_guard=guard,
        )
    )
    runners[WorkflowStage.REVISION] = PersistedCodexStageRunner(
        GuardedDirectGenericProjectQwenRunner(
            enabled=enabled,
            test_profile=test_profile,
            quota_guard=guard,
        )
    )
    return runners
