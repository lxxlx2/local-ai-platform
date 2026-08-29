"""Immutable, read-only workload-aware routing plans.

Plans are recommendations only.  They deliberately expose no runtime control
surface and can never authorize a model lifecycle mutation.  Any future
execution layer must repeat fresh resource and ownership checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

from local_ai_control.services.models import ModelRegistry
from local_ai_control.services.qualification_evidence_store import (
    DeploymentMode,
    QualificationEvidenceStore,
)
from local_ai_control.services.workload_admission import (
    WorkloadAdmissionPolicy,
    WorkloadAdmissionResult,
    WorkloadClass,
    WorkloadManifest,
    WorkloadManifestProbe,
)
from local_ai_control.services.workload_router import (
    DecisionAction,
    EvidenceStatus,
    QualificationEvidence,
    WorkloadAwareRoutingPolicy,
    WorkloadRoutingDecision,
    stress_categories,
)


class PlanningFailureStage(StrEnum):
    INPUT = "INPUT"
    OBSERVATION = "OBSERVATION"
    CANDIDATE_DISCOVERY = "CANDIDATE_DISCOVERY"
    ADMISSION = "ADMISSION"
    EVIDENCE = "EVIDENCE"
    ROUTING = "ROUTING"
    VALIDATION = "VALIDATION"


class WorkloadPlanningError(RuntimeError):
    """A fail-closed planning failure that carries no execution permission."""

    execution_authorized = False

    def __init__(self, stage: PlanningFailureStage, reason: str):
        self.stage = PlanningFailureStage(stage)
        self.reason = str(reason)
        super().__init__(f"{self.stage.value}:{self.reason}")


@dataclass(frozen=True)
class QualificationEvidenceSummary:
    deployment_mode: DeploymentMode
    host_scope_id: str
    profiles: tuple[QualificationEvidence, ...]


@dataclass(frozen=True)
class FallbackCapabilityInputs:
    small_local_qualified_for_workload: bool
    small_local_capability_ready: bool
    cloud_egress_allowed: bool
    cloud_provider_ready: bool


@dataclass(frozen=True)
class WorkloadRoutingPlan:
    task_type: str
    deployment_mode: DeploymentMode
    workload_manifest: WorkloadManifest
    admissions: tuple[WorkloadAdmissionResult, ...]
    admission_observed_at: tuple[tuple[str, str], ...]
    qualification_evidence: QualificationEvidenceSummary
    fallback_inputs: FallbackCapabilityInputs
    routing_decision: WorkloadRoutingDecision
    planned_at: str
    observation_timestamp: str
    execution_authorized: bool = field(default=False, init=False)
    requires_fresh_execution_revalidation: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if self.routing_decision.execution_authorized:
            raise ValueError("routing plan cannot contain execution authorization")


class WorkloadRoutingPlanner:
    """Compose read-only observation, admission, evidence, and routing policy."""

    def __init__(
        self,
        *,
        registry: ModelRegistry | None = None,
        manifest_probe: WorkloadManifestProbe | None = None,
        admission_policy: WorkloadAdmissionPolicy | None = None,
        evidence_store: QualificationEvidenceStore | None = None,
        routing_policy: WorkloadAwareRoutingPolicy | None = None,
    ):
        dependency_registry = (
            getattr(routing_policy, "registry", None)
            or getattr(evidence_store, "registry", None)
        )
        self.registry = registry or dependency_registry or ModelRegistry()
        for dependency in (routing_policy, evidence_store):
            bound_registry = getattr(dependency, "registry", self.registry)
            if bound_registry is not self.registry:
                raise WorkloadPlanningError(
                    PlanningFailureStage.INPUT,
                    "DEPENDENCY_REGISTRY_MISMATCH",
                )

        self.manifest_probe = manifest_probe or WorkloadManifestProbe()
        self.admission_policy = admission_policy or WorkloadAdmissionPolicy()
        self.routing_policy = routing_policy or WorkloadAwareRoutingPolicy(self.registry)
        try:
            self.evidence_store = evidence_store or QualificationEvidenceStore(
                registry=self.registry
            )
        except Exception as error:
            raise WorkloadPlanningError(
                PlanningFailureStage.EVIDENCE,
                f"STORE_VALIDATION_FAILED:{type(error).__name__}",
            ) from error

    @staticmethod
    def _current_manifest(probe: WorkloadManifestProbe) -> WorkloadManifest:
        try:
            observed = probe.capture(WorkloadClass.REPRESENTATIVE_WORKLOAD)
        except Exception as error:
            raise WorkloadPlanningError(
                PlanningFailureStage.OBSERVATION,
                f"MANIFEST_CAPTURE_FAILED:{type(error).__name__}",
            ) from error
        if not isinstance(observed, WorkloadManifest):
            raise WorkloadPlanningError(
                PlanningFailureStage.OBSERVATION,
                "INVALID_MANIFEST_TYPE",
            )
        if (
            observed.workload_class is not WorkloadClass.REPRESENTATIVE_WORKLOAD
            or observed.deliberate_reductions
        ):
            raise WorkloadPlanningError(
                PlanningFailureStage.OBSERVATION,
                "MANIFEST_CLASS_OR_REDUCTION_MISMATCH",
            )
        if stress_categories(observed):
            return replace(observed, workload_class=WorkloadClass.STRESS_COEXISTENCE)
        return observed

    @staticmethod
    def _validate_admission(
        profile_id: str,
        manifest: WorkloadManifest,
        admission: object,
    ) -> WorkloadAdmissionResult:
        if not isinstance(admission, WorkloadAdmissionResult):
            raise WorkloadPlanningError(
                PlanningFailureStage.ADMISSION,
                f"INVALID_ADMISSION_TYPE:{profile_id}",
            )
        if (
            admission.profile_id != profile_id
            or admission.workload_class is not manifest.workload_class
        ):
            raise WorkloadPlanningError(
                PlanningFailureStage.ADMISSION,
                f"ADMISSION_BINDING_MISMATCH:{profile_id}",
            )
        if admission.allowed and not admission.qualification_evidence_eligible:
            raise WorkloadPlanningError(
                PlanningFailureStage.ADMISSION,
                f"ADMISSION_NOT_EVIDENCE_ELIGIBLE:{profile_id}",
            )
        try:
            manifest_time = datetime.fromisoformat(manifest.timestamp)
            admission_time = datetime.fromisoformat(admission.snapshot.timestamp)
        except (TypeError, ValueError) as error:
            raise WorkloadPlanningError(
                PlanningFailureStage.ADMISSION,
                f"INVALID_ADMISSION_TIMESTAMP:{profile_id}",
            ) from error
        if (
            manifest_time.tzinfo is None
            or admission_time.tzinfo is None
            or admission_time < manifest_time
        ):
            raise WorkloadPlanningError(
                PlanningFailureStage.ADMISSION,
                f"STALE_ADMISSION_SNAPSHOT:{profile_id}",
            )
        return admission

    @staticmethod
    def _validate_evidence(
        raw: object,
    ) -> tuple[QualificationEvidence, ...]:
        if not isinstance(raw, dict):
            raise WorkloadPlanningError(
                PlanningFailureStage.EVIDENCE,
                "INVALID_ROUTING_EVIDENCE_TYPE",
            )
        profiles: list[QualificationEvidence] = []
        for profile_id, item in sorted(raw.items()):
            if (
                not isinstance(profile_id, str)
                or not isinstance(item, QualificationEvidence)
                or item.profile_id != profile_id
            ):
                raise WorkloadPlanningError(
                    PlanningFailureStage.EVIDENCE,
                    "EVIDENCE_PROFILE_BINDING_MISMATCH",
                )
            profiles.append(item)
        return tuple(profiles)

    @staticmethod
    def _validate_decision(
        decision: object,
        task_type: str,
        manifest: WorkloadManifest,
        admissions: tuple[WorkloadAdmissionResult, ...],
        evidence: dict[str, QualificationEvidence],
    ) -> WorkloadRoutingDecision:
        if not isinstance(decision, WorkloadRoutingDecision):
            raise WorkloadPlanningError(
                PlanningFailureStage.ROUTING,
                "INVALID_ROUTING_DECISION_TYPE",
            )
        if decision.execution_authorized:
            raise WorkloadPlanningError(
                PlanningFailureStage.VALIDATION,
                "ROUTER_ATTEMPTED_EXECUTION_AUTHORIZATION",
            )
        if (
            decision.task_type != task_type
            or decision.workload_class is not manifest.workload_class
            or decision.stress_categories != stress_categories(manifest)
        ):
            raise WorkloadPlanningError(
                PlanningFailureStage.VALIDATION,
                "DECISION_CONTEXT_BINDING_MISMATCH",
            )

        heavy_actions = {
            DecisionAction.ALLOW_QWEN38,
            DecisionAction.ALLOW_QWEN36,
        }
        admissions_by_id = {item.profile_id: item for item in admissions}
        if decision.action in heavy_actions:
            admission = admissions_by_id.get(decision.profile_id or "")
            if admission is None or not admission.allowed:
                raise WorkloadPlanningError(
                    PlanningFailureStage.VALIDATION,
                    "HEAVY_ALLOW_WITHOUT_EXACT_ADMISSION",
                )
            expected_action = {
                "local-qwen38": DecisionAction.ALLOW_QWEN38,
                "local-qwen36": DecisionAction.ALLOW_QWEN36,
            }.get(decision.profile_id)
            profile_evidence = evidence.get(decision.profile_id or "")
            if (
                expected_action is not decision.action
                or decision.profile_id not in decision.considered
                or profile_evidence is None
                or profile_evidence.profile_id != decision.profile_id
                or WorkloadAwareRoutingPolicy.evidence_status(
                    profile_evidence,
                    manifest,
                )
                is not EvidenceStatus.PASS
            ):
                raise WorkloadPlanningError(
                    PlanningFailureStage.VALIDATION,
                    "HEAVY_ALLOW_WITHOUT_EXACT_QUALIFICATION_EVIDENCE",
                )
        elif decision.profile_id is not None:
            raise WorkloadPlanningError(
                PlanningFailureStage.VALIDATION,
                "FALLBACK_DECISION_HAS_PROFILE",
            )
        return decision

    def plan(
        self,
        *,
        task_type: str,
        deployment_mode: DeploymentMode | str = DeploymentMode.ON_DEMAND_COLD_START,
        small_local_qualified_for_workload: bool = False,
        small_local_capability_ready: bool = False,
        cloud_egress_allowed: bool = False,
        cloud_provider_ready: bool = False,
    ) -> WorkloadRoutingPlan:
        if not isinstance(task_type, str) or not task_type.strip():
            raise WorkloadPlanningError(PlanningFailureStage.INPUT, "TASK_TYPE_REQUIRED")
        normalized_task = task_type.strip().upper()
        try:
            mode = DeploymentMode(deployment_mode)
        except (TypeError, ValueError) as error:
            raise WorkloadPlanningError(
                PlanningFailureStage.INPUT,
                "INVALID_DEPLOYMENT_MODE",
            ) from error
        fallback_values = (
            small_local_qualified_for_workload,
            small_local_capability_ready,
            cloud_egress_allowed,
            cloud_provider_ready,
        )
        if any(type(value) is not bool for value in fallback_values):
            raise WorkloadPlanningError(
                PlanningFailureStage.INPUT,
                "FALLBACK_GATES_MUST_BE_BOOLEAN",
            )

        manifest = self._current_manifest(self.manifest_probe)
        try:
            candidates = tuple(self.routing_policy.candidate_profiles(normalized_task))
        except Exception as error:
            raise WorkloadPlanningError(
                PlanningFailureStage.CANDIDATE_DISCOVERY,
                f"CANDIDATE_DISCOVERY_FAILED:{type(error).__name__}",
            ) from error

        candidate_ids = tuple(profile.profile_id for profile in candidates)
        if len(candidate_ids) != len(set(candidate_ids)) or any(
            profile.local_or_remote != "LOCAL" for profile in candidates
        ):
            raise WorkloadPlanningError(
                PlanningFailureStage.CANDIDATE_DISCOVERY,
                "INVALID_OR_DUPLICATE_LOCAL_CANDIDATE",
            )

        admissions: list[WorkloadAdmissionResult] = []
        for profile in candidates:
            try:
                admission = self.admission_policy.admit(profile, manifest)
            except Exception as error:
                raise WorkloadPlanningError(
                    PlanningFailureStage.ADMISSION,
                    f"ADMISSION_FAILED:{profile.profile_id}:{type(error).__name__}",
                ) from error
            admissions.append(
                self._validate_admission(profile.profile_id, manifest, admission)
            )
        immutable_admissions = tuple(admissions)
        admission_map = {item.profile_id: item for item in immutable_admissions}

        try:
            raw_evidence = self.evidence_store.routing_evidence(deployment_mode=mode)
            evidence_profiles = self._validate_evidence(raw_evidence)
            host_scope_id = self.evidence_store.host_scope.scope_id
            if not isinstance(host_scope_id, str) or not host_scope_id:
                raise ValueError("invalid evidence host scope")
        except WorkloadPlanningError:
            raise
        except Exception as error:
            raise WorkloadPlanningError(
                PlanningFailureStage.EVIDENCE,
                f"EVIDENCE_LOAD_FAILED:{type(error).__name__}",
            ) from error
        evidence_map = {item.profile_id: item for item in evidence_profiles}

        try:
            decision = self.routing_policy.decide(
                task_type=normalized_task,
                manifest=manifest,
                admissions=admission_map,
                evidence=evidence_map,
                small_local_qualified_for_workload=small_local_qualified_for_workload,
                small_local_capability_ready=small_local_capability_ready,
                cloud_egress_allowed=cloud_egress_allowed,
                cloud_provider_ready=cloud_provider_ready,
            )
        except Exception as error:
            raise WorkloadPlanningError(
                PlanningFailureStage.ROUTING,
                f"ROUTING_POLICY_FAILED:{type(error).__name__}",
            ) from error
        validated_decision = self._validate_decision(
            decision,
            normalized_task,
            manifest,
            immutable_admissions,
            evidence_map,
        )

        planned_at = datetime.now(UTC).isoformat()
        return WorkloadRoutingPlan(
            task_type=normalized_task,
            deployment_mode=mode,
            workload_manifest=manifest,
            admissions=immutable_admissions,
            admission_observed_at=tuple(
                (item.profile_id, item.snapshot.timestamp)
                for item in immutable_admissions
            ),
            qualification_evidence=QualificationEvidenceSummary(
                deployment_mode=mode,
                host_scope_id=host_scope_id,
                profiles=evidence_profiles,
            ),
            fallback_inputs=FallbackCapabilityInputs(
                small_local_qualified_for_workload=(
                    small_local_qualified_for_workload
                ),
                small_local_capability_ready=small_local_capability_ready,
                cloud_egress_allowed=cloud_egress_allowed,
                cloud_provider_ready=cloud_provider_ready,
            ),
            routing_decision=validated_decision,
            planned_at=planned_at,
            observation_timestamp=manifest.timestamp,
        )
