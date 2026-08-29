"""Read-only, versioned qualification evidence store.

Qualification outcomes are durable policy inputs.  This loader intentionally
contains no write API: changing PASS/BLOCKED evidence requires a reviewed Git
change to the versioned JSON ledger.  Runtime code therefore cannot promote
itself by mutating local qualification state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import json
from pathlib import Path
import re

from local_ai_control.services.models import ModelRegistry
from local_ai_control.services.workload_admission import WorkloadClass
from local_ai_control.services.workload_router import (
    EvidenceStatus,
    QualificationEvidence,
)


EVIDENCE_PATH = Path("/Users/jerson/AI/config/qualification-evidence-v0.1.json")
SCHEMA_VERSION = "0.1"
POLICY_REVISION = "workload-qualification-v1"
DEFAULT_HOST_SCOPE = "mac-arm64-48g-workstation-v1"
DEFAULT_HOST_PLATFORM = "darwin"
DEFAULT_HOST_ARCH = "arm64"
DEFAULT_HOST_MEMORY_GIB = 48
_ALLOWED_STRESS = frozenset({"IDE", "UNITY"})
_SOURCE_REF = re.compile(r"^github:issue/[1-9][0-9]*#issuecomment-[1-9][0-9]*$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class DeploymentMode(StrEnum):
    ON_DEMAND_COLD_START = "ON_DEMAND_COLD_START"
    PRELOADED_DAEMON = "PRELOADED_DAEMON"


class CandidateRefType(StrEnum):
    GIT_COMMIT = "GIT_COMMIT"
    STAGED_CONTENT_SHA256 = "STAGED_CONTENT_SHA256"


@dataclass(frozen=True)
class HostScope:
    scope_id: str
    platform: str
    arch: str
    memory_gib: int


@dataclass(frozen=True)
class EvidenceRecord:
    profile_id: str
    model_id: str
    deployment_mode: DeploymentMode
    workload_class: WorkloadClass
    stress_category: str | None
    status: EvidenceStatus
    reason: str
    candidate_ref_type: CandidateRefType
    candidate_ref: str
    source_ref: str
    result_artifact: str | None
    recorded_date: str
    deliberate_reductions: tuple[str, ...]

    @property
    def key(self) -> tuple[str, DeploymentMode, WorkloadClass, str | None]:
        return (
            self.profile_id,
            self.deployment_mode,
            self.workload_class,
            self.stress_category,
        )


class QualificationEvidenceStore:
    """Strict loader and adapter for reviewed qualification evidence."""

    def __init__(
        self,
        *,
        registry: ModelRegistry | None = None,
        config_path: Path | str = EVIDENCE_PATH,
        expected_host_scope: str | None = DEFAULT_HOST_SCOPE,
    ):
        self.registry = registry or ModelRegistry()
        self.config_path = Path(config_path)
        self.expected_host_scope = expected_host_scope
        self.host_scope, self.records = self._load()

    @staticmethod
    def _host_scope(raw: object) -> HostScope:
        if not isinstance(raw, dict) or set(raw) != {"id", "platform", "arch", "memory_gib"}:
            raise ValueError("invalid qualification evidence host scope")
        scope_id = raw["id"]
        platform = raw["platform"]
        arch = raw["arch"]
        memory_gib = raw["memory_gib"]
        if not all(isinstance(item, str) and item for item in (scope_id, platform, arch)):
            raise ValueError("invalid qualification evidence host identity")
        if not isinstance(memory_gib, int) or isinstance(memory_gib, bool) or memory_gib <= 0:
            raise ValueError("invalid qualification evidence host memory")
        return HostScope(scope_id, platform, arch, memory_gib)

    def _record(self, raw: object) -> EvidenceRecord:
        required = {
            "profile_id",
            "model_id",
            "deployment_mode",
            "workload_class",
            "stress_category",
            "status",
            "reason",
            "candidate_ref_type",
            "candidate_ref",
            "source_ref",
            "result_artifact",
            "recorded_date",
            "deliberate_reductions",
        }
        if not isinstance(raw, dict) or set(raw) != required:
            raise ValueError("invalid qualification evidence record schema")

        profile_id = raw["profile_id"]
        model_id = raw["model_id"]
        if not isinstance(profile_id, str) or profile_id not in self.registry.models:
            raise ValueError("qualification evidence references unknown profile")
        profile = self.registry.models[profile_id]
        if model_id != profile.model_id:
            raise ValueError("qualification evidence model binding mismatch")

        try:
            deployment_mode = DeploymentMode(raw["deployment_mode"])
            workload_class = WorkloadClass(raw["workload_class"])
            status = EvidenceStatus(raw["status"])
            candidate_ref_type = CandidateRefType(raw["candidate_ref_type"])
        except (TypeError, ValueError) as error:
            raise ValueError("invalid qualification evidence enum value") from error

        # UNKNOWN is represented by absence. Persisted records must be an
        # observed positive qualification or a conservative blocking result.
        if status not in {EvidenceStatus.PASS, EvidenceStatus.BLOCKED}:
            raise ValueError("persisted qualification evidence cannot be UNKNOWN")
        if workload_class is WorkloadClass.LAB:
            raise ValueError("LAB evidence cannot enter production qualification ledger")

        stress_category = raw["stress_category"]
        if workload_class is WorkloadClass.STRESS_COEXISTENCE:
            if stress_category not in _ALLOWED_STRESS:
                raise ValueError("stress evidence requires a supported stress category")
        elif stress_category is not None:
            raise ValueError("representative evidence cannot declare stress category")

        reason = raw["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("qualification evidence reason is required")

        candidate_ref = raw["candidate_ref"]
        if not isinstance(candidate_ref, str):
            raise ValueError("invalid qualification candidate reference")
        if candidate_ref_type is CandidateRefType.GIT_COMMIT:
            if not _HEX40.fullmatch(candidate_ref):
                raise ValueError("GIT_COMMIT evidence requires full 40-hex SHA")
        elif not _HEX64.fullmatch(candidate_ref):
            raise ValueError("STAGED_CONTENT_SHA256 evidence requires 64-hex digest")

        source_ref = raw["source_ref"]
        if not isinstance(source_ref, str) or not _SOURCE_REF.fullmatch(source_ref):
            raise ValueError("qualification evidence requires durable GitHub issue source")

        result_artifact = raw["result_artifact"]
        if result_artifact is not None and (
            not isinstance(result_artifact, str) or not result_artifact.startswith("/")
        ):
            raise ValueError("qualification result artifact must be an absolute path or null")

        recorded_date = raw["recorded_date"]
        try:
            date.fromisoformat(recorded_date)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid qualification evidence recorded date") from error

        reductions = raw["deliberate_reductions"]
        if not isinstance(reductions, list) or any(not isinstance(item, str) for item in reductions):
            raise ValueError("invalid deliberate reductions field")
        normalized_reductions = tuple(item.strip() for item in reductions if item.strip())
        if normalized_reductions:
            raise ValueError("deliberately reduced workload cannot enter qualification ledger")

        return EvidenceRecord(
            profile_id=profile_id,
            model_id=model_id,
            deployment_mode=deployment_mode,
            workload_class=workload_class,
            stress_category=stress_category,
            status=status,
            reason=reason.strip(),
            candidate_ref_type=candidate_ref_type,
            candidate_ref=candidate_ref,
            source_ref=source_ref,
            result_artifact=result_artifact,
            recorded_date=recorded_date,
            deliberate_reductions=normalized_reductions,
        )

    def _load(self) -> tuple[HostScope, tuple[EvidenceRecord, ...]]:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("invalid qualification evidence JSON") from error
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "policy_revision",
            "host_scope",
            "records",
        }:
            raise ValueError("invalid qualification evidence top-level schema")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unsupported qualification evidence schema version")
        if payload["policy_revision"] != POLICY_REVISION:
            raise ValueError("qualification evidence policy revision mismatch")

        host_scope = self._host_scope(payload["host_scope"])
        if self.expected_host_scope is not None and host_scope.scope_id != self.expected_host_scope:
            raise ValueError("qualification evidence host scope mismatch")
        if self.expected_host_scope == DEFAULT_HOST_SCOPE and (
            host_scope.platform != DEFAULT_HOST_PLATFORM
            or host_scope.arch != DEFAULT_HOST_ARCH
            or host_scope.memory_gib != DEFAULT_HOST_MEMORY_GIB
        ):
            raise ValueError("qualification evidence default host attributes mismatch")

        raw_records = payload["records"]
        if not isinstance(raw_records, list):
            raise ValueError("qualification evidence records must be a list")
        records = tuple(self._record(item) for item in raw_records)
        keys = [item.key for item in records]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate qualification evidence key")
        return host_scope, records

    def record_for(
        self,
        profile_id: str,
        *,
        deployment_mode: DeploymentMode | str,
        workload_class: WorkloadClass | str,
        stress_category: str | None = None,
    ) -> EvidenceRecord | None:
        mode = DeploymentMode(deployment_mode)
        workload = WorkloadClass(workload_class)
        matches = [
            item
            for item in self.records
            if item.profile_id == profile_id
            and item.deployment_mode is mode
            and item.workload_class is workload
            and item.stress_category == stress_category
        ]
        if len(matches) > 1:
            raise ValueError("ambiguous qualification evidence")
        return matches[0] if matches else None

    def routing_evidence(
        self,
        *,
        deployment_mode: DeploymentMode | str = DeploymentMode.ON_DEMAND_COLD_START,
    ) -> dict[str, QualificationEvidence]:
        """Compile exact-mode records into Phase D router evidence.

        Missing records remain UNKNOWN. Evidence from another deployment mode
        is never reused implicitly.
        """
        mode = DeploymentMode(deployment_mode)
        grouped: dict[str, QualificationEvidence] = {}
        for profile_id in self.registry.models:
            representative = self.record_for(
                profile_id,
                deployment_mode=mode,
                workload_class=WorkloadClass.REPRESENTATIVE_WORKLOAD,
            )
            stress = []
            for category in sorted(_ALLOWED_STRESS):
                item = self.record_for(
                    profile_id,
                    deployment_mode=mode,
                    workload_class=WorkloadClass.STRESS_COEXISTENCE,
                    stress_category=category,
                )
                if item is not None:
                    stress.append((category, item.status))
            if representative is not None or stress:
                grouped[profile_id] = QualificationEvidence(
                    profile_id=profile_id,
                    representative=(
                        representative.status
                        if representative is not None
                        else EvidenceStatus.UNKNOWN
                    ),
                    stress=tuple(stress),
                )
        return grouped
