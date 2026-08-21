"""Workflow Supervisor V0.1 public API.

Round-1 hardening is split by responsibility so durable private payloads,
repository state, runners, Codex adapter, and state-machine policy can be
reviewed independently while callers keep the original import surface.
"""
from .supervisor_contracts import *  # noqa: F401,F403
from .supervisor_repository import SupervisorRepository
from .supervisor_runners import (
    GitGateRunner, LocalValidationRunner, MockCodexRunner, MockReviewRunner,
    SafeCommandPolicy, SecurityRunner, StaticPassRunner,
)
from .supervisor_codex import (
    CodexCapability, CodexCapabilityProbe, CodexTaskRunner,
    PersistedCodexStageRunner, RealCodexRunner,
)
from .supervisor_workflow import WorkflowSupervisor


def default_demo_runners(real_validation=True):
    validation = LocalValidationRunner() if real_validation else StaticPassRunner("Mock local validation passed")
    return {
        WorkflowStage.INTAKE: StaticPassRunner("Intake schema validated"),
        WorkflowStage.PRODUCER: MockCodexRunner(),
        WorkflowStage.VALIDATION: validation,
        WorkflowStage.SELF_ACCEPTANCE: StaticPassRunner("Deterministic self acceptance passed"),
        WorkflowStage.REVIEW: MockReviewRunner(),
        WorkflowStage.REVISION: MockCodexRunner(),
        WorkflowStage.SECURITY: SecurityRunner(),
        WorkflowStage.GIT_GATE: GitGateRunner(),
    }
