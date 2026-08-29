"""Read-only workload-aware routing decisions.

This module intentionally does not start, stop, signal, or reconfigure model
runtimes.  It combines existing registry eligibility, resource admission, and
qualification evidence into a recommendation that a later, separately
approved execution layer may consume.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from local_ai_control.services.models import ModelRegistry, ModelRole
from local_ai_control.services.workload_admission import (
    WorkloadAdmissionResult,
    WorkloadClass,
    WorkloadManifest,
)


class DecisionAction(StrEnum):
    ALLOW_QWEN38 = "ALLOW_QWEN38"
    ALLOW_QWEN36 = "ALLOW_QWEN36"
    ALLOW_SMALL_LOCAL = "ALLOW_SMALL_LOCAL"
    USE_CLOUD = "USE_CLOUD"
    QUEUE_TASK = "QUEUE_TASK"


class EvidenceStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    PASS = "PASS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class QualificationEvidence:
    profile_id: str
    representative: EvidenceStatus = EvidenceStatus.UNKNOWN
    stress: tuple[tuple[str, EvidenceStatus], ...] = ()

    def stress_status(self, category: str) -> EvidenceStatus:
        matches = [status for name, status in self.stress if name == category]
        if len(matches) > 1:
            raise ValueError(f"duplicate stress evidence for {category}")
        return matches[0] if matches else EvidenceStatus.UNKNOWN


@dataclass(frozen=True)
class WorkloadRoutingDecision:
    action: DecisionAction
    task_type: str
    workload_class: WorkloadClass
    profile_id: str | None
    reason: str
    stress_categories: tuple[str, ...]
    considered: tuple[str, ...]
    execution_authorized: bool = False


_TASK_ROLE = {
    "CHAT": ModelRole.MAIN,
    "MAIN": ModelRole.MAIN,
    "FAST": ModelRole.FAST,
    "CHAT_FAST": ModelRole.FAST,
    "CODE": ModelRole.CODE,
    "REVIEW": ModelRole.REVIEW,
    "VISION": ModelRole.VISION,
    "VIDEO_UNDERSTANDING": ModelRole.VIDEO_UNDERSTANDING,
    "DEEP_REASONING": ModelRole.DEEP,
}

_STRESS_APPLICATIONS = frozenset({"UNITY", "IDE"})


def _stress_categories(manifest: WorkloadManifest) -> tuple[str, ...]:
    return tuple(sorted({
        app.category
        for app in manifest.material_applications
        if app.category in _STRESS_APPLICATIONS
    }))


class WorkloadAwareRoutingPolicy:
    """Produce a conservative routing recommendation without side effects."""

    def __init__(self, registry: ModelRegistry | None = None):
        self.registry = registry or ModelRegistry()

    def _candidates(self, task_type: str):
        normalized = str(task_type).upper()
        if normalized not in _TASK_ROLE:
            return normalized, ()
        role = _TASK_ROLE[normalized]
        roles = (
            (role, ModelRole.FALLBACK, ModelRole.FAST)
            if normalized in {"CHAT", "MAIN"}
            else (role,)
        )
        seen: set[str] = set()
        result = []
        for candidate_role in roles:
            for profile in self.registry.eligible(candidate_role):
                if profile.profile_id in seen:
                    continue
                seen.add(profile.profile_id)
                result.append(profile)
        return normalized, tuple(result)

    @staticmethod
    def _evidence_status(
        evidence: QualificationEvidence | None,
        manifest: WorkloadManifest,
        stress_categories: tuple[str, ...],
    ) -> EvidenceStatus:
        if evidence is None:
            return EvidenceStatus.UNKNOWN
        if manifest.workload_class is WorkloadClass.LAB:
            # LAB results can diagnose capability but never authorize a
            # production-oriented routing recommendation.
            return EvidenceStatus.UNKNOWN
        if stress_categories:
            statuses = tuple(evidence.stress_status(category) for category in stress_categories)
            if any(status is EvidenceStatus.BLOCKED for status in statuses):
                return EvidenceStatus.BLOCKED
            if statuses and all(status is EvidenceStatus.PASS for status in statuses):
                return EvidenceStatus.PASS
            return EvidenceStatus.UNKNOWN
        return evidence.representative

    @staticmethod
    def _fallback(
        *,
        task_type: str,
        manifest: WorkloadManifest,
        stress_categories: tuple[str, ...],
        considered: tuple[str, ...],
        small_local_ready: bool,
        cloud_egress_allowed: bool,
        reason: str,
    ) -> WorkloadRoutingDecision:
        if small_local_ready:
            return WorkloadRoutingDecision(
                DecisionAction.ALLOW_SMALL_LOCAL,
                task_type,
                manifest.workload_class,
                None,
                reason,
                stress_categories,
                considered,
            )
        if cloud_egress_allowed:
            return WorkloadRoutingDecision(
                DecisionAction.USE_CLOUD,
                task_type,
                manifest.workload_class,
                None,
                reason,
                stress_categories,
                considered,
            )
        return WorkloadRoutingDecision(
            DecisionAction.QUEUE_TASK,
            task_type,
            manifest.workload_class,
            None,
            reason,
            stress_categories,
            considered,
        )

    def decide(
        self,
        *,
        task_type: str,
        manifest: WorkloadManifest,
        admissions: Mapping[str, WorkloadAdmissionResult],
        evidence: Mapping[str, QualificationEvidence],
        small_local_ready: bool = False,
        cloud_egress_allowed: bool = False,
    ) -> WorkloadRoutingDecision:
        normalized, candidates = self._candidates(task_type)
        stress_categories = _stress_categories(manifest)

        if manifest.deliberate_reductions or manifest.workload_class is WorkloadClass.LAB:
            return self._fallback(
                task_type=normalized,
                manifest=manifest,
                stress_categories=stress_categories,
                considered=(),
                small_local_ready=small_local_ready,
                cloud_egress_allowed=cloud_egress_allowed,
                reason="LAB_OR_REDUCED_WORKLOAD_NOT_PRODUCTION_EVIDENCE",
            )

        if not candidates:
            return self._fallback(
                task_type=normalized,
                manifest=manifest,
                stress_categories=stress_categories,
                considered=(),
                small_local_ready=small_local_ready,
                cloud_egress_allowed=cloud_egress_allowed,
                reason="NO_ELIGIBLE_LOCAL_PROFILE_FOR_TASK",
            )

        considered: list[str] = []
        saw_resource_block = False
        saw_evidence_block = False
        saw_unknown_evidence = False

        for profile in candidates:
            considered.append(profile.profile_id)
            admission = admissions.get(profile.profile_id)
            if admission is None or admission.workload_class is not manifest.workload_class:
                saw_resource_block = True
                continue
            if not admission.allowed:
                saw_resource_block = True
                continue

            status = self._evidence_status(
                evidence.get(profile.profile_id),
                manifest,
                stress_categories,
            )
            if status is EvidenceStatus.BLOCKED:
                saw_evidence_block = True
                continue
            if status is not EvidenceStatus.PASS:
                saw_unknown_evidence = True
                continue

            if profile.profile_id == "local-qwen38":
                action = DecisionAction.ALLOW_QWEN38
            elif profile.profile_id == "local-qwen36":
                action = DecisionAction.ALLOW_QWEN36
            else:
                # New local profiles require an explicit decision action before
                # this policy may recommend them.
                saw_unknown_evidence = True
                continue

            return WorkloadRoutingDecision(
                action=action,
                task_type=normalized,
                workload_class=manifest.workload_class,
                profile_id=profile.profile_id,
                reason="QUALIFICATION_AND_RESOURCE_GATES_PASS",
                stress_categories=stress_categories,
                considered=tuple(considered),
            )

        if saw_evidence_block:
            reason = "QUALIFICATION_EVIDENCE_BLOCKED_CURRENT_WORKLOAD"
        elif saw_unknown_evidence:
            reason = "QUALIFICATION_EVIDENCE_UNKNOWN_CURRENT_WORKLOAD"
        elif saw_resource_block:
            reason = "RESOURCE_ADMISSION_NOT_AVAILABLE"
        else:
            reason = "NO_LOCAL_DECISION"

        return self._fallback(
            task_type=normalized,
            manifest=manifest,
            stress_categories=stress_categories,
            considered=tuple(considered),
            small_local_ready=small_local_ready,
            cloud_egress_allowed=cloud_egress_allowed,
            reason=reason,
        )
