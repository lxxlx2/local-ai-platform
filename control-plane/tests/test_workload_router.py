from local_ai_control.services.models import MemorySnapshot, ModelRegistry
from local_ai_control.services.workload_admission import (
    MaterialApplication,
    WorkloadAdmissionResult,
    WorkloadClass,
    WorkloadManifest,
)
from local_ai_control.services.workload_router import (
    DecisionAction,
    EvidenceStatus,
    QualificationEvidence,
    WorkloadAwareRoutingPolicy,
)


def snapshot(*, pressure="NORMAL", reclaimable=32.0, swap=1.0):
    return MemorySnapshot(
        total_gib=48.0,
        available_gib=reclaimable,
        swap_used_gib=swap,
        pressure=pressure,
        reclaimable_gib=reclaimable,
    )


def manifest(*, workload_class=WorkloadClass.REPRESENTATIVE_WORKLOAD, apps=(), reductions=()):
    return WorkloadManifest(
        workload_class=workload_class,
        timestamp="2026-08-29T00:00:00+00:00",
        memory=snapshot(),
        top_processes=(),
        material_applications=tuple(
            MaterialApplication(category=name, process_count=1, rss_mib=1024.0)
            for name in apps
        ),
        fixed_port_listeners=((8000, ()), (8001, ()), (8011, ())),
        deliberate_reductions=tuple(reductions),
    )


def admission(profile_id, *, allowed=True, workload_class=WorkloadClass.REPRESENTATIVE_WORKLOAD):
    return WorkloadAdmissionResult(
        profile_id=profile_id,
        workload_class=workload_class,
        allowed=allowed,
        qualification_evidence_eligible=allowed,
        reason="RESOURCE_ADMISSION_PASS" if allowed else "RESOURCE_PREFLIGHT_DENIED",
        preflight_reason="OK" if allowed else "INSUFFICIENT_RECLAIMABLE_MEMORY",
        snapshot=snapshot(),
    )


def registry():
    return ModelRegistry(aliases={
        "MAIN": {
            "profile": "local-qwen38",
            "status": "QUALIFIED",
            "max_context_tokens": 16384,
        },
        "FAST": {"profile": "local-qwen36", "status": "QUALIFIED"},
        "FALLBACK": {"profile": "local-qwen36", "status": "QUALIFIED"},
        "VISION": {"profile": "local-qwen38", "status": "QUALIFIED"},
        "REVIEW": {"profile": "local-qwen36", "status": "QUALIFIED"},
    })


def policy():
    return WorkloadAwareRoutingPolicy(registry())


def normal_admissions():
    return {
        "local-qwen38": admission("local-qwen38"),
        "local-qwen36": admission("local-qwen36"),
    }


def normal_evidence(*, qwen38=EvidenceStatus.UNKNOWN, qwen36=EvidenceStatus.UNKNOWN):
    return {
        "local-qwen38": QualificationEvidence("local-qwen38", representative=qwen38),
        "local-qwen36": QualificationEvidence("local-qwen36", representative=qwen36),
    }


def cloud_ready_kwargs():
    return {"cloud_egress_allowed": True, "cloud_provider_ready": True}


def stress_admissions():
    return {
        "local-qwen38": admission(
            "local-qwen38", workload_class=WorkloadClass.STRESS_COEXISTENCE
        ),
        "local-qwen36": admission(
            "local-qwen36", workload_class=WorkloadClass.STRESS_COEXISTENCE
        ),
    }


def test_representative_chat_prefers_qwen38_when_qualified_and_admitted():
    decision = policy().decide(
        task_type="CHAT",
        manifest=manifest(apps=("BROWSER",)),
        admissions=normal_admissions(),
        evidence=normal_evidence(qwen38=EvidenceStatus.PASS, qwen36=EvidenceStatus.PASS),
    )
    assert decision.action is DecisionAction.ALLOW_QWEN38
    assert decision.profile_id == "local-qwen38"
    assert decision.execution_authorized is False


def test_representative_chat_falls_to_qwen36_when_qwen38_evidence_blocked():
    decision = policy().decide(
        task_type="CHAT",
        manifest=manifest(apps=("BROWSER",)),
        admissions=normal_admissions(),
        evidence=normal_evidence(qwen38=EvidenceStatus.BLOCKED, qwen36=EvidenceStatus.PASS),
    )
    assert decision.action is DecisionAction.ALLOW_QWEN36
    assert decision.profile_id == "local-qwen36"
    assert decision.considered == ("local-qwen38", "local-qwen36")


def test_fast_role_selects_only_qwen36():
    decision = policy().decide(
        task_type="FAST",
        manifest=manifest(apps=("BROWSER",)),
        admissions=normal_admissions(),
        evidence=normal_evidence(qwen38=EvidenceStatus.PASS, qwen36=EvidenceStatus.PASS),
    )
    assert decision.action is DecisionAction.ALLOW_QWEN36
    assert decision.considered == ("local-qwen36",)


def test_stress_workload_requires_stress_specific_evidence():
    current = manifest(
        workload_class=WorkloadClass.STRESS_COEXISTENCE,
        apps=("BROWSER", "IDE"),
    )
    decision = policy().decide(
        task_type="CHAT",
        manifest=current,
        admissions=stress_admissions(),
        evidence=normal_evidence(qwen38=EvidenceStatus.PASS, qwen36=EvidenceStatus.PASS),
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.stress_categories == ("IDE",)
    assert decision.reason == "QUALIFICATION_EVIDENCE_UNKNOWN_CURRENT_WORKLOAD"


def test_qwen36_may_be_selected_after_matching_stress_pass():
    current = manifest(
        workload_class=WorkloadClass.STRESS_COEXISTENCE,
        apps=("BROWSER", "IDE"),
    )
    evidence = {
        "local-qwen38": QualificationEvidence(
            "local-qwen38",
            stress=(("IDE", EvidenceStatus.BLOCKED),),
        ),
        "local-qwen36": QualificationEvidence(
            "local-qwen36",
            representative=EvidenceStatus.PASS,
            stress=(("IDE", EvidenceStatus.PASS),),
        ),
    }
    decision = policy().decide(
        task_type="CHAT",
        manifest=current,
        admissions=stress_admissions(),
        evidence=evidence,
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.ALLOW_QWEN36
    assert decision.profile_id == "local-qwen36"


def test_multiple_stress_categories_all_require_pass():
    current = manifest(
        workload_class=WorkloadClass.STRESS_COEXISTENCE,
        apps=("BROWSER", "IDE", "UNITY"),
    )
    evidence = {
        "local-qwen36": QualificationEvidence(
            "local-qwen36",
            stress=(("IDE", EvidenceStatus.PASS),),
        )
    }
    decision = policy().decide(
        task_type="FAST",
        manifest=current,
        admissions=stress_admissions(),
        evidence=evidence,
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.stress_categories == ("IDE", "UNITY")


def test_small_local_requires_both_workload_and_task_capability_readiness():
    current = manifest(workload_class=WorkloadClass.STRESS_COEXISTENCE, apps=("IDE",))
    decision = policy().decide(
        task_type="FAST",
        manifest=current,
        admissions=stress_admissions(),
        evidence={},
        small_local_qualified_for_workload=True,
        small_local_capability_ready=True,
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.ALLOW_SMALL_LOCAL


def test_small_local_workload_pass_without_task_capability_does_not_route():
    current = manifest(workload_class=WorkloadClass.STRESS_COEXISTENCE, apps=("IDE",))
    decision = policy().decide(
        task_type="FAST",
        manifest=current,
        admissions=stress_admissions(),
        evidence={},
        small_local_qualified_for_workload=True,
        small_local_capability_ready=False,
    )
    assert decision.action is DecisionAction.QUEUE_TASK


def test_lab_workload_never_authorizes_heavy_model_from_qualification_evidence():
    current = manifest(workload_class=WorkloadClass.LAB, reductions=("closed browser",))
    admissions = {
        "local-qwen38": admission("local-qwen38", workload_class=WorkloadClass.LAB),
        "local-qwen36": admission("local-qwen36", workload_class=WorkloadClass.LAB),
    }
    decision = policy().decide(
        task_type="CHAT",
        manifest=current,
        admissions=admissions,
        evidence=normal_evidence(qwen38=EvidenceStatus.PASS, qwen36=EvidenceStatus.PASS),
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.reason == "LAB_OR_REDUCED_WORKLOAD_NOT_PRODUCTION_EVIDENCE"


def test_resource_denial_prevents_heavy_model_selection():
    admissions = {
        "local-qwen38": admission("local-qwen38", allowed=False),
        "local-qwen36": admission("local-qwen36", allowed=False),
    }
    decision = policy().decide(
        task_type="CHAT",
        manifest=manifest(apps=("BROWSER",)),
        admissions=admissions,
        evidence=normal_evidence(qwen38=EvidenceStatus.PASS, qwen36=EvidenceStatus.PASS),
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.reason == "RESOURCE_ADMISSION_NOT_AVAILABLE"


def test_admission_from_different_workload_class_is_not_reused():
    current = manifest(workload_class=WorkloadClass.STRESS_COEXISTENCE, apps=("IDE",))
    decision = policy().decide(
        task_type="FAST",
        manifest=current,
        admissions={"local-qwen36": admission("local-qwen36")},
        evidence={
            "local-qwen36": QualificationEvidence(
                "local-qwen36", stress=(("IDE", EvidenceStatus.PASS),)
            )
        },
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.reason == "RESOURCE_ADMISSION_NOT_AVAILABLE"


def test_admission_dictionary_key_cannot_spoof_another_profile():
    admissions = {
        "local-qwen38": admission("local-qwen36"),
        "local-qwen36": admission("local-qwen36"),
    }
    decision = policy().decide(
        task_type="CHAT",
        manifest=manifest(apps=("BROWSER",)),
        admissions=admissions,
        evidence=normal_evidence(qwen38=EvidenceStatus.PASS, qwen36=EvidenceStatus.PASS),
    )
    assert decision.action is DecisionAction.ALLOW_QWEN36
    assert decision.profile_id == "local-qwen36"


def test_evidence_dictionary_key_cannot_spoof_another_profile():
    decision = policy().decide(
        task_type="FAST",
        manifest=manifest(apps=("BROWSER",)),
        admissions=normal_admissions(),
        evidence={
            "local-qwen36": QualificationEvidence(
                "local-qwen38", representative=EvidenceStatus.PASS
            )
        },
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.reason == "QUALIFICATION_EVIDENCE_INVALID"


def test_vision_does_not_silently_fallback_to_qwen36():
    decision = policy().decide(
        task_type="VISION",
        manifest=manifest(apps=("BROWSER",)),
        admissions=normal_admissions(),
        evidence=normal_evidence(qwen38=EvidenceStatus.UNKNOWN, qwen36=EvidenceStatus.PASS),
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.considered == ("local-qwen38",)


def test_unsupported_task_does_not_invent_local_capability():
    decision = policy().decide(
        task_type="SOMETHING_NEW",
        manifest=manifest(apps=("BROWSER",)),
        admissions=normal_admissions(),
        evidence=normal_evidence(qwen38=EvidenceStatus.PASS, qwen36=EvidenceStatus.PASS),
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.reason == "NO_ELIGIBLE_LOCAL_PROFILE_FOR_TASK"


def test_cloud_requires_both_egress_permission_and_ready_provider():
    kwargs = dict(
        task_type="VISION",
        manifest=manifest(apps=("BROWSER",)),
        admissions=normal_admissions(),
        evidence={},
    )
    no_egress = policy().decide(
        **kwargs,
        cloud_egress_allowed=False,
        cloud_provider_ready=True,
    )
    no_provider = policy().decide(
        **kwargs,
        cloud_egress_allowed=True,
        cloud_provider_ready=False,
    )
    ready = policy().decide(
        **kwargs,
        cloud_egress_allowed=True,
        cloud_provider_ready=True,
    )
    assert no_egress.action is DecisionAction.QUEUE_TASK
    assert no_provider.action is DecisionAction.QUEUE_TASK
    assert ready.action is DecisionAction.USE_CLOUD


def test_duplicate_stress_evidence_fails_closed_to_nonlocal_decision():
    current = manifest(workload_class=WorkloadClass.STRESS_COEXISTENCE, apps=("IDE",))
    evidence = {
        "local-qwen36": QualificationEvidence(
            "local-qwen36",
            stress=(
                ("IDE", EvidenceStatus.PASS),
                ("IDE", EvidenceStatus.PASS),
            ),
        )
    }
    decision = policy().decide(
        task_type="FAST",
        manifest=current,
        admissions=stress_admissions(),
        evidence=evidence,
        **cloud_ready_kwargs(),
    )
    assert decision.action is DecisionAction.USE_CLOUD
    assert decision.reason == "QUALIFICATION_EVIDENCE_INVALID"
