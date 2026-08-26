from __future__ import annotations

from pathlib import Path

from .codex_quota_guard import CodexQuotaGuard
from .direct_local_qwen_verified import VerifiedDirectGenericProjectQwenRunner
from .generic_project_persisted import GenericPersistedStageRunner
from .generic_project_policy import TestProfile
from .supervisor_contracts import StageResult, StageResultStatus, WorkflowStage
from .supervisor_generic_project import generic_project_runners


LOCAL_QWEN_ENDPOINT = "http://127.0.0.1:8001"


class GuardedDirectGenericProjectQwenRunner(VerifiedDirectGenericProjectQwenRunner):
    """Verified Direct Local-Qwen executor with deterministic local-route attestation.

    Runtime authorization is based on the executor route and capabilities, not on
    correlation with account-wide OpenAI quota movement. The production provider
    must be bound to the exact localhost Qwen sidecar. The task path never starts
    Codex CLI or Codex app-server. Codex quota telemetry remains available as an
    out-of-band diagnostic, but it is not part of the mutating task lifecycle.
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
        # Kept only for API compatibility with older callers/tests. It is never
        # invoked from a Direct-Qwen task because doing so would itself start a
        # Codex app-server process and account-wide quota changes are not
        # attributable to this executor.
        self.quota_guard = quota_guard

    def _route_attestation(self) -> StageResult | None:
        endpoint = getattr(self.provider, "base_url", None)
        if endpoint != LOCAL_QWEN_ENDPOINT:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Direct Local Qwen provider route is not the qualified localhost sidecar",
                error="LOCAL_EXECUTOR_ROUTE_DENIED",
                metrics={
                    "provider_type": type(self.provider).__name__,
                    "local_route_attestation": "FAIL",
                    "qualified_endpoint": LOCAL_QWEN_ENDPOINT,
                    "codex_cli_invoked": False,
                    "codex_app_server_invoked": False,
                    "execution_started": False,
                },
            )
        return None

    def run_task(self, spec, execution_id: str) -> StageResult:
        denied = self._route_attestation()
        if denied is not None:
            return denied

        result = super().run_task(spec, execution_id)
        result.metrics.update(
            {
                "local_route_attestation": "PASS",
                "qualified_endpoint": LOCAL_QWEN_ENDPOINT,
                "codex_cli_invoked": False,
                "codex_app_server_invoked": False,
                "codex_quota_telemetry": "OUT_OF_BAND_ONLY",
                "local_executor": "direct-qwen-tools-verified",
                "execution_started": True,
            }
        )
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
    runners[WorkflowStage.PRODUCER] = GenericPersistedStageRunner(
        GuardedDirectGenericProjectQwenRunner(
            enabled=enabled,
            test_profile=test_profile,
            quota_guard=quota_guard,
        )
    )
    runners[WorkflowStage.REVISION] = GenericPersistedStageRunner(
        GuardedDirectGenericProjectQwenRunner(
            enabled=enabled,
            test_profile=test_profile,
            quota_guard=quota_guard,
        )
    )
    return runners
