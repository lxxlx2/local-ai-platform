from contextlib import contextmanager
from datetime import UTC, datetime
import inspect
import threading

import pytest

from local_ai_control.services.models import (
    MemorySnapshot,
    ModelRegistry,
    QWEN36,
)
from local_ai_control.services.qualification_evidence_store import (
    DeploymentMode,
)
from local_ai_control.services.runtime_providers import (
    HeavyModelConflict,
    RuntimeProviderFactory,
)
from local_ai_control.services.workload_admission import (
    WorkloadAdmissionResult,
    WorkloadClass,
    WorkloadManifest,
)
from local_ai_control.services.workload_execution import (
    WorkloadAwareExecutionCoordinator,
    WorkloadExecutionDeferred,
    WorkloadExecutionError,
)
from local_ai_control.services.workload_planner import (
    FallbackCapabilityInputs,
    QualificationEvidenceSummary,
    WorkloadRoutingPlan,
)
from local_ai_control.services.workload_router import (
    DecisionAction,
    EvidenceStatus,
    QualificationEvidence,
    WorkloadRoutingDecision,
)


def _plan(
    action=DecisionAction.ALLOW_QWEN36,
    profile_id="local-qwen36",
    *,
    task_type="CHAT",
):
    now = datetime.now(UTC).isoformat()

    memory = MemorySnapshot(
        total_gib=48,
        available_gib=30,
        swap_used_gib=1,
        pressure="NORMAL",
        reclaimable_gib=30,
        timestamp=now,
    )

    manifest = WorkloadManifest(
        workload_class=(
            WorkloadClass.REPRESENTATIVE_WORKLOAD
        ),
        timestamp=now,
        memory=memory,
        top_processes=(),
        material_applications=(),
        fixed_port_listeners=(),
    )

    admissions = ()
    evidence_profiles = ()
    considered = ()

    if profile_id is not None:
        admissions = (
            WorkloadAdmissionResult(
                profile_id=profile_id,
                workload_class=manifest.workload_class,
                allowed=True,
                qualification_evidence_eligible=True,
                reason="OK",
                preflight_reason="OK",
                snapshot=memory,
            ),
        )
        evidence_profiles = (
            QualificationEvidence(
                profile_id=profile_id,
                representative=EvidenceStatus.PASS,
            ),
        )
        considered = (profile_id,)

    decision = WorkloadRoutingDecision(
        action=action,
        task_type=task_type,
        workload_class=manifest.workload_class,
        profile_id=profile_id,
        reason="TEST",
        stress_categories=(),
        considered=considered,
    )

    return WorkloadRoutingPlan(
        task_type=task_type,
        deployment_mode=(
            DeploymentMode.ON_DEMAND_COLD_START
        ),
        workload_manifest=manifest,
        admissions=admissions,
        admission_observed_at=tuple(
            (item.profile_id, item.snapshot.timestamp)
            for item in admissions
        ),
        qualification_evidence=(
            QualificationEvidenceSummary(
                deployment_mode=(
                    DeploymentMode.ON_DEMAND_COLD_START
                ),
                host_scope_id="test-host",
                profiles=evidence_profiles,
            )
        ),
        fallback_inputs=FallbackCapabilityInputs(
            False,
            False,
            False,
            False,
        ),
        routing_decision=decision,
        planned_at=now,
        observation_timestamp=now,
    )


class FakePlanner:
    def __init__(self, registry, plans):
        self.registry = registry
        self.plans = list(plans)
        self.calls = []

    def plan(self, **kwargs):
        self.calls.append(kwargs)
        if not self.plans:
            raise AssertionError("unexpected extra plan")
        item = self.plans.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeProvider:
    def __init__(self):
        self.calls = []

    def generate(
        self,
        prompt,
        max_output_tokens=1024,
    ):
        self.calls.append(
            (prompt, max_output_tokens)
        )
        return "ok"


class FakeRuntime:
    def __init__(self, registry):
        self.registry = registry
        self.lock = threading.RLock()
        self.targets = []
        self.provider = FakeProvider()

    @contextmanager
    def exact_profile_session(
        self,
        profile_id,
        task_type,
    ):
        self.targets.append(
            (profile_id, task_type)
        )
        yield self.provider


def _coordinator(plan):
    registry = ModelRegistry()
    planner = FakePlanner(
        registry,
        [plan],
    )
    runtime = FakeRuntime(registry)

    return (
        WorkloadAwareExecutionCoordinator(
            planner=planner,
            runtime=runtime,
        ),
        planner,
        runtime,
    )


def test_execution_coordinator_does_not_accept_stale_plan_argument():
    parameters = inspect.signature(
        WorkloadAwareExecutionCoordinator.session
    ).parameters

    assert "plan" not in parameters


def test_fresh_qwen36_plan_executes_only_exact_qwen36():
    coordinator, planner, runtime = _coordinator(
        _plan()
    )

    with coordinator.session(
        task_type="CHAT"
    ) as provider:
        assert provider is runtime.provider

    assert len(planner.calls) == 1
    assert runtime.targets == [
        ("local-qwen36", "CHAT")
    ]


def test_fresh_qwen38_plan_executes_only_exact_qwen38():
    coordinator, planner, runtime = _coordinator(
        _plan(
            DecisionAction.ALLOW_QWEN38,
            "local-qwen38",
        )
    )

    with coordinator.session(
        task_type="CHAT"
    ):
        pass

    assert len(planner.calls) == 1
    assert runtime.targets == [
        ("local-qwen38", "CHAT")
    ]


@pytest.mark.parametrize(
    "action",
    (
        DecisionAction.QUEUE_TASK,
        DecisionAction.USE_CLOUD,
        DecisionAction.ALLOW_SMALL_LOCAL,
    ),
)
def test_non_heavy_phase_f1_routes_fail_closed(action):
    coordinator, _, runtime = _coordinator(
        _plan(
            action,
            None,
        )
    )

    with pytest.raises(
        WorkloadExecutionDeferred
    ):
        with coordinator.session(
            task_type="CHAT"
        ):
            pass

    assert runtime.targets == []


def test_planning_failure_never_reaches_runtime():
    registry = ModelRegistry()

    planner = FakePlanner(
        registry,
        [RuntimeError("boom")],
    )
    runtime = FakeRuntime(registry)

    coordinator = WorkloadAwareExecutionCoordinator(
        planner=planner,
        runtime=runtime,
    )

    with pytest.raises(
        WorkloadExecutionError
    ):
        with coordinator.session(
            task_type="CHAT"
        ):
            pass

    assert runtime.targets == []


def test_profile_action_mismatch_fails_closed():
    coordinator, _, runtime = _coordinator(
        _plan(
            DecisionAction.ALLOW_QWEN36,
            "local-qwen38",
        )
    )

    with pytest.raises(
        WorkloadExecutionError
    ):
        with coordinator.session(
            task_type="CHAT"
        ):
            pass

    assert runtime.targets == []


def test_dependency_registry_mismatch_is_rejected():
    planner_registry = ModelRegistry()
    runtime_registry = ModelRegistry()

    planner = FakePlanner(
        planner_registry,
        [_plan()],
    )
    runtime = FakeRuntime(
        runtime_registry
    )

    with pytest.raises(
        WorkloadExecutionError
    ):
        WorkloadAwareExecutionCoordinator(
            planner=planner,
            runtime=runtime,
        )


def test_generate_uses_fresh_execution_path_without_implicit_failover():
    coordinator, planner, runtime = _coordinator(
        _plan()
    )

    result = coordinator.generate(
        task_type="CHAT",
        prompt="hello",
        max_output_tokens=32,
    )

    assert result == "ok"
    assert len(planner.calls) == 1
    assert runtime.targets == [
        ("local-qwen36", "CHAT")
    ]
    assert runtime.provider.calls == [
        ("hello", 32)
    ]


class HealthyProvider:
    def __init__(self, healthy):
        self.healthy = healthy

    def health(self):
        if not self.healthy:
            raise OSError("down")
        return {"status": "healthy"}


class OwnershipProofLifecycle:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def transition_source_state(
        self,
        profile_id,
        endpoint_probes,
    ):
        self.calls.append(profile_id)
        assert set(endpoint_probes) == {
            "local-qwen38",
            "local-qwen36",
        }
        return self.state


class UnusedPreflight:
    def check(self, *_args, **_kwargs):
        raise AssertionError(
            "healthy reuse should not start a model"
        )


def test_exact_profile_session_proves_healthy_target_ownership():
    registry = ModelRegistry()
    lifecycle = OwnershipProofLifecycle(
        "OWNED"
    )

    main = HealthyProvider(False)
    fast = HealthyProvider(True)

    runtime = RuntimeProviderFactory(
        registry,
        main=main,
        fast=fast,
        lifecycle=lifecycle,
        preflight=UnusedPreflight(),
        sleep=lambda _: None,
    )

    with runtime.exact_profile_session(
        QWEN36.profile_id,
        "CHAT",
    ) as provider:
        assert provider is fast

    assert lifecycle.calls == [
        QWEN36.profile_id
    ]


def test_exact_profile_session_rejects_healthy_but_unowned_target():
    registry = ModelRegistry()
    lifecycle = OwnershipProofLifecycle(
        "ABSENT"
    )

    runtime = RuntimeProviderFactory(
        registry,
        main=HealthyProvider(False),
        fast=HealthyProvider(True),
        lifecycle=lifecycle,
        preflight=UnusedPreflight(),
        sleep=lambda _: None,
    )

    with pytest.raises(
        HeavyModelConflict
    ):
        with runtime.exact_profile_session(
            QWEN36.profile_id,
            "CHAT",
        ):
            pass
