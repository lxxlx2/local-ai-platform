from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path

import pytest

import local_ai_control.services.workload_planner as planner_module

from local_ai_control.services.models import (
    MemorySnapshot,
    ModelRegistry,
)
from local_ai_control.services.qualification_evidence_store import (
    DeploymentMode,
    QualificationEvidenceStore,
)
from local_ai_control.services.workload_admission import (
    MaterialApplication,
    WorkloadAdmissionResult,
    WorkloadClass,
    WorkloadManifest,
)
from local_ai_control.services.workload_planner import (
    PlanningFailureStage,
    WorkloadPlanningError,
    WorkloadRoutingPlan,
    WorkloadRoutingPlanner,
)
from local_ai_control.services.workload_router import (
    DecisionAction,
    WorkloadAwareRoutingPolicy,
    WorkloadRoutingDecision,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_CONFIG = REPO_ROOT / "config/qualification-evidence-v0.1.json"


def snapshot(*, timestamp="2026-08-29T10:01:00+00:00"):
    return MemorySnapshot(
        total_gib=48.0,
        available_gib=32.0,
        swap_used_gib=1.0,
        pressure="NORMAL",
        reclaimable_gib=32.0,
        timestamp=timestamp,
    )


def manifest(*, apps=(), workload_class=WorkloadClass.REPRESENTATIVE_WORKLOAD):
    return WorkloadManifest(
        workload_class=workload_class,
        timestamp="2026-08-29T10:00:00+00:00",
        memory=snapshot(timestamp="2026-08-29T10:00:00+00:00"),
        top_processes=(),
        material_applications=tuple(
            MaterialApplication(category=name, process_count=1, rss_mib=1024.0)
            for name in apps
        ),
        fixed_port_listeners=((8000, ()), (8001, ()), (8011, ())),
    )


class FakeManifestProbe:
    def __init__(self, value=None, error=None):
        self.value = value or manifest(apps=("BROWSER",))
        self.error = error
        self.calls = []

    def capture(self, workload_class):
        self.calls.append(workload_class)
        if self.error:
            raise self.error
        return self.value


class FreshAdmissionPolicy:
    def __init__(self, *, error_profile=None, mismatch_class=False, stale=False):
        self.error_profile = error_profile
        self.mismatch_class = mismatch_class
        self.stale = stale
        self.calls = []

    def admit(self, profile, current_manifest):
        self.calls.append((profile.profile_id, current_manifest.workload_class))
        if profile.profile_id == self.error_profile:
            raise RuntimeError("synthetic admission failure")
        workload_class = (
            WorkloadClass.REPRESENTATIVE_WORKLOAD
            if self.mismatch_class
            else current_manifest.workload_class
        )
        index = len(self.calls)
        return WorkloadAdmissionResult(
            profile_id=profile.profile_id,
            workload_class=workload_class,
            allowed=True,
            qualification_evidence_eligible=True,
            reason="RESOURCE_ADMISSION_PASS",
            preflight_reason="OK",
            snapshot=snapshot(
                timestamp=(
                    "2026-08-29T09:59:00+00:00"
                    if self.stale
                    else f"2026-08-29T10:01:0{index}+00:00"
                )
            ),
        )


def registry():
    return ModelRegistry(
        aliases={
            "MAIN": {
                "profile": "local-qwen38",
                "status": "QUALIFIED",
                "max_context_tokens": 16384,
            },
            "FAST": {"profile": "local-qwen36", "status": "QUALIFIED"},
            "FALLBACK": {"profile": "local-qwen36", "status": "QUALIFIED"},
            "VISION": {"profile": "local-qwen38", "status": "QUALIFIED"},
            "REVIEW": {"profile": "local-qwen36", "status": "QUALIFIED"},
        }
    )


def planner(*, probe=None, admission=None, store=None, routing=None):
    current_registry = registry()
    current_store = store or QualificationEvidenceStore(
        registry=current_registry,
        config_path=EVIDENCE_CONFIG,
    )
    return WorkloadRoutingPlanner(
        registry=current_registry,
        manifest_probe=probe or FakeManifestProbe(),
        admission_policy=admission or FreshAdmissionPolicy(),
        evidence_store=current_store,
        routing_policy=routing or WorkloadAwareRoutingPolicy(current_registry),
    )


def test_real_durable_representative_evidence_plans_qwen36():
    plan = planner().plan(task_type="CHAT")

    assert plan.routing_decision.action is DecisionAction.ALLOW_QWEN36
    assert plan.routing_decision.profile_id == "local-qwen36"
    qwen38 = next(
        item
        for item in plan.qualification_evidence.profiles
        if item.profile_id == "local-qwen38"
    )
    assert qwen38.representative.value == "BLOCKED"
    assert plan.execution_authorized is False
    assert plan.routing_decision.execution_authorized is False
    assert plan.requires_fresh_execution_revalidation is True


@pytest.mark.parametrize("stress_app", ["IDE", "UNITY"])
def test_unknown_stress_evidence_never_allows_heavy_local(stress_app):
    current = FakeManifestProbe(manifest(apps=("BROWSER", stress_app)))
    plan = planner(probe=current).plan(task_type="CHAT")

    assert plan.workload_manifest.workload_class is WorkloadClass.STRESS_COEXISTENCE
    assert plan.routing_decision.action is DecisionAction.QUEUE_TASK
    assert plan.routing_decision.profile_id is None
    assert plan.routing_decision.stress_categories == (stress_app,)


def test_preloaded_evidence_does_not_leak_into_on_demand():
    plan = planner().plan(
        task_type="CHAT",
        deployment_mode=DeploymentMode.PRELOADED_DAEMON,
    )

    assert plan.deployment_mode is DeploymentMode.PRELOADED_DAEMON
    assert plan.qualification_evidence.profiles == ()
    assert plan.routing_decision.action is DecisionAction.QUEUE_TASK


def test_evidence_store_validation_failure_fails_closed(monkeypatch):
    def invalid_store(**kwargs):
        raise ValueError("synthetic ledger validation failure")

    monkeypatch.setattr(planner_module, "QualificationEvidenceStore", invalid_store)
    with pytest.raises(WorkloadPlanningError) as raised:
        WorkloadRoutingPlanner(
            registry=registry(),
            evidence_store=None,
        )
    assert raised.value.stage is PlanningFailureStage.EVIDENCE
    assert "STORE_VALIDATION_FAILED" in raised.value.reason
    assert raised.value.execution_authorized is False


def test_manifest_probe_failure_fails_closed():
    with pytest.raises(WorkloadPlanningError) as raised:
        planner(probe=FakeManifestProbe(error=OSError("synthetic"))).plan(
            task_type="CHAT"
        )
    assert raised.value.stage is PlanningFailureStage.OBSERVATION
    assert raised.value.execution_authorized is False


def test_partial_candidate_admission_failure_fails_closed():
    policy = FreshAdmissionPolicy(error_profile="local-qwen36")
    with pytest.raises(WorkloadPlanningError) as raised:
        planner(admission=policy).plan(task_type="CHAT")
    assert raised.value.stage is PlanningFailureStage.ADMISSION
    assert policy.calls == [
        ("local-qwen38", WorkloadClass.REPRESENTATIVE_WORKLOAD),
        ("local-qwen36", WorkloadClass.REPRESENTATIVE_WORKLOAD),
    ]


@pytest.mark.parametrize(
    ("qualified", "capable", "expected"),
    [
        (False, False, DecisionAction.QUEUE_TASK),
        (True, False, DecisionAction.QUEUE_TASK),
        (False, True, DecisionAction.QUEUE_TASK),
        (True, True, DecisionAction.ALLOW_SMALL_LOCAL),
    ],
)
def test_small_local_requires_both_explicit_gates(qualified, capable, expected):
    plan = planner(probe=FakeManifestProbe(manifest(apps=("IDE",)))).plan(
        task_type="CHAT",
        small_local_qualified_for_workload=qualified,
        small_local_capability_ready=capable,
    )
    assert plan.routing_decision.action is expected


@pytest.mark.parametrize(
    ("egress", "ready", "expected"),
    [
        (False, False, DecisionAction.QUEUE_TASK),
        (True, False, DecisionAction.QUEUE_TASK),
        (False, True, DecisionAction.QUEUE_TASK),
        (True, True, DecisionAction.USE_CLOUD),
    ],
)
def test_cloud_requires_privacy_and_provider_gates(egress, ready, expected):
    plan = planner(probe=FakeManifestProbe(manifest(apps=("UNITY",)))).plan(
        task_type="CHAT",
        cloud_egress_allowed=egress,
        cloud_provider_ready=ready,
    )
    assert plan.routing_decision.action is expected
    assert plan.fallback_inputs.cloud_egress_allowed is egress
    assert plan.fallback_inputs.cloud_provider_ready is ready


def test_unsupported_task_does_not_invent_capability():
    plan = planner().plan(task_type="UNSUPPORTED_NEW_TASK")
    assert plan.admissions == ()
    assert plan.routing_decision.action is DecisionAction.QUEUE_TASK
    assert plan.routing_decision.reason == "NO_ELIGIBLE_LOCAL_PROFILE_FOR_TASK"


def test_admissions_are_exactly_profile_bound_with_fresh_provenance():
    plan = planner().plan(task_type="CHAT")
    assert tuple(item.profile_id for item in plan.admissions) == (
        "local-qwen38",
        "local-qwen36",
    )
    assert plan.admission_observed_at == (
        ("local-qwen38", "2026-08-29T10:01:01+00:00"),
        ("local-qwen36", "2026-08-29T10:01:02+00:00"),
    )
    assert all(
        timestamp != plan.observation_timestamp
        for _, timestamp in plan.admission_observed_at
    )


def test_mismatched_workload_class_admission_fails_closed():
    with pytest.raises(WorkloadPlanningError) as raised:
        planner(
            probe=FakeManifestProbe(manifest(apps=("IDE",))),
            admission=FreshAdmissionPolicy(mismatch_class=True),
        ).plan(task_type="CHAT")
    assert raised.value.stage is PlanningFailureStage.ADMISSION
    assert "ADMISSION_BINDING_MISMATCH" in raised.value.reason


def test_stale_admission_snapshot_fails_closed():
    with pytest.raises(WorkloadPlanningError) as raised:
        planner(admission=FreshAdmissionPolicy(stale=True)).plan(task_type="CHAT")
    assert raised.value.stage is PlanningFailureStage.ADMISSION
    assert "STALE_ADMISSION_SNAPSHOT" in raised.value.reason


@pytest.mark.parametrize("field", [
    "small_local_qualified_for_workload",
    "small_local_capability_ready",
    "cloud_egress_allowed",
    "cloud_provider_ready",
])
def test_fallback_gates_reject_truthy_non_booleans(field):
    with pytest.raises(WorkloadPlanningError, match="FALLBACK_GATES_MUST_BE_BOOLEAN"):
        planner().plan(task_type="CHAT", **{field: "false"})


def test_plans_are_immutable_and_never_execution_authority():
    plan = planner().plan(task_type="CHAT")
    with pytest.raises(FrozenInstanceError):
        plan.execution_authorized = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        replace(plan, execution_authorized=True)
    unsafe_decision = replace(plan.routing_decision, execution_authorized=True)
    with pytest.raises(ValueError, match="cannot contain execution authorization"):
        replace(plan, routing_decision=unsafe_decision)
    assert not hasattr(plan, "execute")
    assert not hasattr(plan, "apply")
    assert not hasattr(plan, "start")
    assert not hasattr(plan, "switch")


def test_router_cannot_inject_execution_authorization():
    class UnsafeRoutingPolicy(WorkloadAwareRoutingPolicy):
        def decide(self, **kwargs):
            return WorkloadRoutingDecision(
                action=DecisionAction.ALLOW_QWEN36,
                task_type="CHAT",
                workload_class=kwargs["manifest"].workload_class,
                profile_id="local-qwen36",
                reason="unsafe",
                stress_categories=(),
                considered=("local-qwen36",),
                execution_authorized=True,
            )

    current_registry = registry()
    unsafe = UnsafeRoutingPolicy(current_registry)
    with pytest.raises(WorkloadPlanningError, match="EXECUTION_AUTHORIZATION"):
        WorkloadRoutingPlanner(
            registry=current_registry,
            manifest_probe=FakeManifestProbe(),
            admission_policy=FreshAdmissionPolicy(),
            evidence_store=QualificationEvidenceStore(
                registry=current_registry,
                config_path=EVIDENCE_CONFIG,
            ),
            routing_policy=unsafe,
        ).plan(task_type="CHAT")


def test_router_cannot_inject_heavy_allow_without_pass_evidence():
    class UnsafeRoutingPolicy(WorkloadAwareRoutingPolicy):
        def decide(self, **kwargs):
            return WorkloadRoutingDecision(
                action=DecisionAction.ALLOW_QWEN38,
                task_type="CHAT",
                workload_class=kwargs["manifest"].workload_class,
                profile_id="local-qwen38",
                reason="unsafe",
                stress_categories=(),
                considered=("local-qwen38",),
            )

    current_registry = registry()
    with pytest.raises(WorkloadPlanningError, match="EXACT_QUALIFICATION_EVIDENCE"):
        WorkloadRoutingPlanner(
            registry=current_registry,
            manifest_probe=FakeManifestProbe(),
            admission_policy=FreshAdmissionPolicy(),
            evidence_store=QualificationEvidenceStore(
                registry=current_registry,
                config_path=EVIDENCE_CONFIG,
            ),
            routing_policy=UnsafeRoutingPolicy(current_registry),
        ).plan(task_type="CHAT")


def test_planner_source_has_no_runtime_control_surface():
    source = inspect.getsource(inspect.getmodule(WorkloadRoutingPlanner))
    forbidden = (
        "subprocess.Popen",
        "launchctl",
        "killpg",
        "pkill",
        "killall",
        "osascript",
        "runtime.start",
        "runtime.stop",
        "RuntimeProviderFactory",
    )
    assert all(item not in source for item in forbidden)
    public_methods = {
        name
        for name, member in inspect.getmembers(
            WorkloadRoutingPlanner,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    assert public_methods == {"plan"}
