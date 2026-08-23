"""Workflow Supervisor V0.1 public API with Round-2 hardening."""
from .supervisor_contracts import *  # noqa: F401,F403
from .supervisor_runners import (
    GitGateRunner, LocalValidationRunner, MockCodexRunner, MockReviewRunner,
    SafeCommandPolicy, StaticPassRunner,
)
from .supervisor_codex import (
    CodexCapability, CodexCapabilityProbe, CodexTaskRunner,
    PersistedCodexStageRunner, RealCodexRunner,
)
from .local_producer_supervisor import SupervisorLocalProducerTaskRunner
from .supervisor_round2 import (
    DurableReviewRunner, LeaseKeepingRunner, PersistedReviewSubmission,
    ReviewTaskSpec, ReviewerWorkUnit, Round2SecurityRunner,
    Round2SupervisorRepository, Round2WorkflowSupervisor,
    TaskObjective, recursive_private_sanitize,
)

SupervisorRepository = Round2SupervisorRepository
WorkflowSupervisor = Round2WorkflowSupervisor
SecurityRunner = Round2SecurityRunner


def default_demo_runners(real_validation=True):
    validation = LocalValidationRunner() if real_validation else StaticPassRunner("Mock local validation passed")
    return {
        WorkflowStage.INTAKE: StaticPassRunner("Intake schema validated"),
        WorkflowStage.PRODUCER: MockCodexRunner(),
        WorkflowStage.VALIDATION: validation,
        WorkflowStage.SELF_ACCEPTANCE: StaticPassRunner("Deterministic self acceptance passed"),
        WorkflowStage.REVIEW: DurableReviewRunner(),
        WorkflowStage.REVISION: MockCodexRunner(),
        WorkflowStage.SECURITY: Round2SecurityRunner(),
        WorkflowStage.GIT_GATE: GitGateRunner(),
    }


def local_producer_runners(real_validation=True, provider=None):
    """Opt-in local Producer set. Default daemon behavior remains unchanged.

    Qwen3.8 can propose/apply only policy-validated patches. Validation, review,
    security, and Git Gate remain separate stages; this function does not deploy,
    commit, push, merge, or enable nested Codex execution.
    """
    validation = LocalValidationRunner() if real_validation else StaticPassRunner("Mock local validation passed")
    return {
        WorkflowStage.INTAKE: StaticPassRunner("Intake schema validated"),
        WorkflowStage.PRODUCER: PersistedCodexStageRunner(SupervisorLocalProducerTaskRunner(provider)),
        WorkflowStage.VALIDATION: validation,
        WorkflowStage.SELF_ACCEPTANCE: StaticPassRunner("Deterministic self acceptance passed"),
        WorkflowStage.REVIEW: DurableReviewRunner(),
        WorkflowStage.REVISION: PersistedCodexStageRunner(SupervisorLocalProducerTaskRunner(provider)),
        WorkflowStage.SECURITY: Round2SecurityRunner(),
        WorkflowStage.GIT_GATE: GitGateRunner(),
    }
