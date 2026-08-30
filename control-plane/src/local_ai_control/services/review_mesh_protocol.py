"""Deterministic core contracts for Review Mesh Protocol V1.

G0-A slice 1 implements only:

- canonical encoding and digests
- campaign-common context
- exact campaign identity
- lane-specific review requests
- monotonic generation binding
- same-campaign retry semantics

It deliberately performs no:

- provider/model call
- qualification decision
- reviewer identity admission
- quorum evaluation
- ledger write
- fixer execution
- runtime mutation

The existing Workflow Supervisor remains authoritative for
TaskObjective, ReviewTaskSpec, CandidateIdentity and safe manifests.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
import re
import secrets


PROTOCOL_VERSION = "REVIEW_MESH_PROTOCOL_V1"

_SHA40 = re.compile(r"[a-f0-9]{40}")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_IDENTIFIER = re.compile(
    r"[A-Za-z0-9_.:/+@-]{1,256}"
)
_NONCE = re.compile(
    r"[a-f0-9]{32,128}"
)


class RiskLevel(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class PrivacyClass(str, Enum):
    PUBLIC = "PUBLIC"
    RESTRICTED = "RESTRICTED"
    PRIVATE = "PRIVATE"


class ReviewerClass(str, Enum):
    P3 = "P3"
    P2 = "P2"
    STRONG_P1 = "STRONG_P1"
    STRONG_P0 = "STRONG_P0"


def _require_identifier(
    value: str,
    label: str,
) -> str:
    value = str(value)

    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{label} is invalid"
        )

    return value


def _require_sha40(
    value: str,
    label: str,
) -> str:
    value = str(value)

    if not _SHA40.fullmatch(value):
        raise ValueError(
            f"{label} must be an exact lowercase 40-hex SHA"
        )

    return value


def _require_sha256(
    value: str,
    label: str,
) -> str:
    value = str(value)

    if not _SHA256.fullmatch(value):
        raise ValueError(
            f"{label} must be an exact lowercase SHA-256 digest"
        )

    return value


def _require_nonce(
    value: str,
) -> str:
    value = str(value)

    if not _NONCE.fullmatch(value):
        raise ValueError(
            "request nonce must contain at least 128 bits "
            "of lowercase hexadecimal material"
        )

    return value


def _parse_timestamp(
    value: str,
    label: str,
) -> datetime:
    try:
        timestamp = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError as error:
        raise ValueError(
            f"{label} must be RFC3339/ISO8601"
        ) from error

    if timestamp.tzinfo is None:
        raise ValueError(
            f"{label} must be timezone-aware"
        )

    return timestamp


def canonical_json_bytes(
    value,
) -> bytes:
    """Return canonical JSON bytes used by V1 digests.

    V1 protocol objects themselves control their exact field set,
    so unknown-field rejection occurs when constructing those typed
    objects rather than inside this generic encoder.
    """

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            "value is not canonical-json compatible"
        ) from error

    return encoded.encode("utf-8")


def canonical_digest(
    value,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(value)
    ).hexdigest()


def new_request_nonce() -> str:
    return secrets.token_hex(16)


@dataclass(frozen=True)
class CampaignContextV1:
    """Fields shared by every reviewer lane in one campaign.

    Lane-specific fields MUST NOT appear here. Therefore this
    object's digest is stable across independent reviewers.
    """

    repository_id: str
    task_id: str
    source_work_unit_id: str

    review_round: int
    candidate_generation: int
    review_generation: int

    objective_sha256: str
    objective_manifest_hash: str

    candidate_identity_digest: str
    base_sha: str
    candidate_sha: str
    candidate_diff_sha256: str

    review_scope_manifest_digest: str
    reviewed_material_digest: str
    contributor_set_digest: str
    local_gate_evidence_digest: str

    policy_revision: str
    policy_decision_digest: str

    risk_level: RiskLevel
    risk_decision_digest: str

    privacy_class: PrivacyClass
    egress_decision_digest: str
    privacy_decision_digest: str

    required_reviewer_class: ReviewerClass
    quorum_policy_digest: str

    lineage_registry_snapshot_digest: str
    qualification_registry_snapshot_digest: str

    benchmark_harness_policy_revision: str
    campaign_retry_policy_digest: str

    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(
                "protocol version mismatch"
            )

        for label, value in (
            (
                "repository id",
                self.repository_id,
            ),
            (
                "task id",
                self.task_id,
            ),
            (
                "source work unit id",
                self.source_work_unit_id,
            ),
            (
                "policy revision",
                self.policy_revision,
            ),
            (
                "benchmark/harness policy revision",
                self.benchmark_harness_policy_revision,
            ),
        ):
            _require_identifier(
                value,
                label,
            )

        for label, value in (
            (
                "review round",
                self.review_round,
            ),
            (
                "candidate generation",
                self.candidate_generation,
            ),
            (
                "review generation",
                self.review_generation,
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                or value >= 2**64
            ):
                raise ValueError(
                    f"{label} must be an unsigned positive 64-bit integer"
                )

        for label, value in (
            (
                "objective digest",
                self.objective_sha256,
            ),
            (
                "objective manifest digest",
                self.objective_manifest_hash,
            ),
            (
                "candidate identity digest",
                self.candidate_identity_digest,
            ),
            (
                "candidate diff digest",
                self.candidate_diff_sha256,
            ),
            (
                "review scope manifest digest",
                self.review_scope_manifest_digest,
            ),
            (
                "reviewed material digest",
                self.reviewed_material_digest,
            ),
            (
                "contributor set digest",
                self.contributor_set_digest,
            ),
            (
                "local gate evidence digest",
                self.local_gate_evidence_digest,
            ),
            (
                "policy decision digest",
                self.policy_decision_digest,
            ),
            (
                "risk decision digest",
                self.risk_decision_digest,
            ),
            (
                "egress decision digest",
                self.egress_decision_digest,
            ),
            (
                "privacy decision digest",
                self.privacy_decision_digest,
            ),
            (
                "quorum policy digest",
                self.quorum_policy_digest,
            ),
            (
                "lineage registry snapshot digest",
                self.lineage_registry_snapshot_digest,
            ),
            (
                "qualification registry snapshot digest",
                self.qualification_registry_snapshot_digest,
            ),
            (
                "campaign retry policy digest",
                self.campaign_retry_policy_digest,
            ),
        ):
            _require_sha256(
                value,
                label,
            )

        _require_sha40(
            self.base_sha,
            "base SHA",
        )

        _require_sha40(
            self.candidate_sha,
            "candidate SHA",
        )

        if not isinstance(
            self.risk_level,
            RiskLevel,
        ):
            raise ValueError(
                "invalid risk level"
            )

        if not isinstance(
            self.privacy_class,
            PrivacyClass,
        ):
            raise ValueError(
                "invalid privacy class"
            )

        if not isinstance(
            self.required_reviewer_class,
            ReviewerClass,
        ):
            raise ValueError(
                "invalid reviewer class"
            )

    def stable_mapping(self) -> dict:
        return {
            "protocol_version":
                self.protocol_version,

            "repository_id":
                self.repository_id,

            "task_id":
                self.task_id,

            "source_work_unit_id":
                self.source_work_unit_id,

            "review_round":
                self.review_round,

            "candidate_generation":
                self.candidate_generation,

            "review_generation":
                self.review_generation,

            "objective_sha256":
                self.objective_sha256,

            "objective_manifest_hash":
                self.objective_manifest_hash,

            "candidate_identity_digest":
                self.candidate_identity_digest,

            "base_sha":
                self.base_sha,

            "candidate_sha":
                self.candidate_sha,

            "candidate_diff_sha256":
                self.candidate_diff_sha256,

            "review_scope_manifest_digest":
                self.review_scope_manifest_digest,

            "reviewed_material_digest":
                self.reviewed_material_digest,

            "contributor_set_digest":
                self.contributor_set_digest,

            "local_gate_evidence_digest":
                self.local_gate_evidence_digest,

            "policy_revision":
                self.policy_revision,

            "policy_decision_digest":
                self.policy_decision_digest,

            "risk_level":
                self.risk_level.value,

            "risk_decision_digest":
                self.risk_decision_digest,

            "privacy_class":
                self.privacy_class.value,

            "egress_decision_digest":
                self.egress_decision_digest,

            "privacy_decision_digest":
                self.privacy_decision_digest,

            "required_reviewer_class":
                self.required_reviewer_class.value,

            "quorum_policy_digest":
                self.quorum_policy_digest,

            "lineage_registry_snapshot_digest":
                self.lineage_registry_snapshot_digest,

            "qualification_registry_snapshot_digest":
                self.qualification_registry_snapshot_digest,

            "benchmark_harness_policy_revision":
                self.benchmark_harness_policy_revision,

            "campaign_retry_policy_digest":
                self.campaign_retry_policy_digest,
        }

    @property
    def campaign_context_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )

    @property
    def review_campaign_id(
        self,
    ) -> str:
        return (
            "rc1:"
            + self.campaign_context_digest
        )

    def new_review_generation(
        self,
        **changes,
    ) -> "CampaignContextV1":
        """Create a semantically new campaign.

        The caller cannot accidentally reuse the current generation.
        """

        requested = changes.pop(
            "review_generation",
            self.review_generation + 1,
        )

        if requested <= self.review_generation:
            raise ValueError(
                "new campaign must advance review generation"
            )

        return replace(
            self,
            review_generation=requested,
            **changes,
        )

    def new_candidate_generation(
        self,
        *,
        candidate_sha: str,
        candidate_identity_digest: str,
        candidate_diff_sha256: str,
        contributor_set_digest: str,
        base_sha: str | None = None,
    ) -> "CampaignContextV1":
        """Bind a later candidate publication.

        Review generation also advances because candidate context changed.
        """

        return replace(
            self,
            candidate_generation=(
                self.candidate_generation + 1
            ),
            review_generation=(
                self.review_generation + 1
            ),
            candidate_sha=candidate_sha,
            candidate_identity_digest=(
                candidate_identity_digest
            ),
            candidate_diff_sha256=(
                candidate_diff_sha256
            ),
            contributor_set_digest=(
                contributor_set_digest
            ),
            base_sha=(
                self.base_sha
                if base_sha is None
                else base_sha
            ),
        )


@dataclass(frozen=True)
class ReviewRequestV1:
    """One lane-specific reviewer invocation request."""

    campaign: CampaignContextV1

    review_work_unit_id: str
    reviewer_lane: str
    required_adapter_principal: str

    lane_attempt: int
    request_nonce: str

    created_at: str
    request_expiry_at: str

    def __post_init__(self):
        for label, value in (
            (
                "review work unit id",
                self.review_work_unit_id,
            ),
            (
                "reviewer lane",
                self.reviewer_lane,
            ),
            (
                "required adapter principal",
                self.required_adapter_principal,
            ),
        ):
            _require_identifier(
                value,
                label,
            )

        if (
            isinstance(self.lane_attempt, bool)
            or not isinstance(
                self.lane_attempt,
                int,
            )
            or self.lane_attempt < 1
        ):
            raise ValueError(
                "lane attempt must be a positive integer"
            )

        _require_nonce(
            self.request_nonce
        )

        created = _parse_timestamp(
            self.created_at,
            "request creation timestamp",
        )

        expiry = _parse_timestamp(
            self.request_expiry_at,
            "request expiry timestamp",
        )

        if expiry <= created:
            raise ValueError(
                "request expiry must be after creation"
            )

    def stable_mapping(self) -> dict:
        return {
            **self.campaign.stable_mapping(),

            "review_campaign_id":
                self.campaign.review_campaign_id,

            "campaign_context_digest":
                self.campaign.campaign_context_digest,

            "review_work_unit_id":
                self.review_work_unit_id,

            "reviewer_lane":
                self.reviewer_lane,

            "required_adapter_principal":
                self.required_adapter_principal,

            "lane_attempt":
                self.lane_attempt,

            "request_nonce":
                self.request_nonce,

            "created_at":
                self.created_at,

            "request_expiry_at":
                self.request_expiry_at,
        }

    @property
    def request_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )

    @property
    def review_request_id(
        self,
    ) -> str:
        return (
            "rr1:"
            + self.request_digest
        )

    def retry(
        self,
        *,
        review_work_unit_id: str,
        request_nonce: str,
        created_at: str,
        request_expiry_at: str,
    ) -> "ReviewRequestV1":
        """Retry one failed lane without invalidating campaign votes.

        Campaign identity and review generation are preserved.
        Lane attempt and invocation-bound fields advance.
        """

        return ReviewRequestV1(
            campaign=self.campaign,

            review_work_unit_id=(
                review_work_unit_id
            ),

            reviewer_lane=(
                self.reviewer_lane
            ),

            required_adapter_principal=(
                self.required_adapter_principal
            ),

            lane_attempt=(
                self.lane_attempt + 1
            ),

            request_nonce=request_nonce,

            created_at=created_at,

            request_expiry_at=(
                request_expiry_at
            ),
        )

    def same_campaign_as(
        self,
        other: "ReviewRequestV1",
    ) -> bool:
        return (
            self.campaign.review_campaign_id
            == other.campaign.review_campaign_id
            and
            self.campaign.review_generation
            == other.campaign.review_generation
            and
            self.campaign.candidate_generation
            == other.campaign.candidate_generation
        )


class ExecutionLocality(str, Enum):
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"


class ReviewVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


_FINDING_SCOPES = frozenset({
    "FILE",
    "WORKFLOW",
})

_FINDING_SEVERITIES = frozenset({
    "BLOCKING",
    "HIGH",
    "MEDIUM",
    "LOW",
})


def _require_text(
    value: str,
    label: str,
    *,
    max_bytes: int = 16384,
) -> str:
    value = str(value)

    if (
        not value
        or len(value.encode("utf-8")) > max_bytes
    ):
        raise ValueError(
            f"{label} is empty or exceeds size bound"
        )

    return value


def _normalize_findings(
    findings: tuple[dict, ...],
) -> tuple[dict, ...]:
    if len(findings) > 100:
        raise ValueError(
            "review findings exceed bound"
        )

    normalized = []

    exact_keys = {
        "scope",
        "severity",
        "file",
        "evidence",
        "recommended_fix",
    }

    for raw in findings:
        if (
            not isinstance(raw, dict)
            or set(raw) != exact_keys
        ):
            raise ValueError(
                "review finding fields are invalid"
            )

        scope = str(raw["scope"])
        severity = str(raw["severity"])

        if scope not in _FINDING_SCOPES:
            raise ValueError(
                "review finding scope is invalid"
            )

        if severity not in _FINDING_SEVERITIES:
            raise ValueError(
                "review finding severity is invalid"
            )

        file_value = raw["file"]

        if file_value is not None:
            file_value = _require_text(
                file_value,
                "review finding file",
                max_bytes=4096,
            )

        evidence = _require_text(
            raw["evidence"],
            "review finding evidence",
        )

        recommended_fix = _require_text(
            raw["recommended_fix"],
            "review finding recommended fix",
        )

        normalized.append({
            "scope": scope,
            "severity": severity,
            "file": file_value,
            "evidence": evidence,
            "recommended_fix": recommended_fix,
        })

    return tuple(normalized)


@dataclass(frozen=True)
class IdentityEnvelopeV1:
    """Trusted observed execution identity.

    This object is created from orchestrator/adapter observations.
    Model prose cannot authoritatively supply these fields.
    """

    authenticated_adapter_principal: str
    authentication_method: str
    credential_version: str

    provider_principal: str
    provider_account_scope: str

    requested_model_id: str
    requested_endpoint: str

    actual_model_id: str

    fallback_model_id: str | None
    fallback_reason: str

    serving_backend: str

    foundation_model: str
    foundation_lineage_class: str
    foundation_revision: str

    hosted_copy_relationship: str
    derivative_relationship: str

    execution_locality: ExecutionLocality
    actual_egress_destination: str

    invocation_id: str
    execution_receipt_digest: str

    request_nonce: str
    reviewed_material_digest: str
    review_request_digest: str

    candidate_generation: int

    invocation_started_at: str
    invocation_completed_at: str

    privacy_decision_digest: str

    lineage_registry_snapshot_digest: str
    qualification_registry_snapshot_digest: str
    qualification_evidence_digest: str

    orchestrator_ingested_at: str
    authenticated_ingestion_receipt_digest: str

    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(
                "identity protocol version mismatch"
            )

        for label, value in (
            (
                "authenticated adapter principal",
                self.authenticated_adapter_principal,
            ),
            (
                "authentication method",
                self.authentication_method,
            ),
            (
                "credential version",
                self.credential_version,
            ),
            (
                "provider principal",
                self.provider_principal,
            ),
            (
                "provider account scope",
                self.provider_account_scope,
            ),
            (
                "requested model id",
                self.requested_model_id,
            ),
            (
                "requested endpoint",
                self.requested_endpoint,
            ),
            (
                "actual model id",
                self.actual_model_id,
            ),
            (
                "serving backend",
                self.serving_backend,
            ),
            (
                "foundation model",
                self.foundation_model,
            ),
            (
                "foundation lineage class",
                self.foundation_lineage_class,
            ),
            (
                "foundation revision",
                self.foundation_revision,
            ),
            (
                "hosted copy relationship",
                self.hosted_copy_relationship,
            ),
            (
                "derivative relationship",
                self.derivative_relationship,
            ),
            (
                "actual egress destination",
                self.actual_egress_destination,
            ),
            (
                "invocation id",
                self.invocation_id,
            ),
        ):
            _require_identifier(
                value,
                label,
            )

        if self.fallback_model_id is not None:
            _require_identifier(
                self.fallback_model_id,
                "fallback model id",
            )

        _require_identifier(
            self.fallback_reason,
            "fallback reason",
        )

        if (
            self.actual_model_id
            == self.requested_model_id
        ):
            if (
                self.fallback_model_id is not None
                or self.fallback_reason != "NO_FALLBACK"
            ):
                raise ValueError(
                    "no-fallback execution has inconsistent fallback fields"
                )
        else:
            if (
                self.fallback_model_id
                != self.actual_model_id
                or self.fallback_reason == "NO_FALLBACK"
            ):
                raise ValueError(
                    "unexpected actual model requires explicit registered fallback"
                )

        if not isinstance(
            self.execution_locality,
            ExecutionLocality,
        ):
            raise ValueError(
                "execution locality is invalid"
            )

        for label, value in (
            (
                "execution receipt digest",
                self.execution_receipt_digest,
            ),
            (
                "reviewed material digest",
                self.reviewed_material_digest,
            ),
            (
                "review request digest",
                self.review_request_digest,
            ),
            (
                "privacy decision digest",
                self.privacy_decision_digest,
            ),
            (
                "lineage registry snapshot digest",
                self.lineage_registry_snapshot_digest,
            ),
            (
                "qualification registry snapshot digest",
                self.qualification_registry_snapshot_digest,
            ),
            (
                "qualification evidence digest",
                self.qualification_evidence_digest,
            ),
            (
                "authenticated ingestion receipt digest",
                self.authenticated_ingestion_receipt_digest,
            ),
        ):
            _require_sha256(
                value,
                label,
            )

        _require_nonce(
            self.request_nonce
        )

        if (
            isinstance(
                self.candidate_generation,
                bool,
            )
            or not isinstance(
                self.candidate_generation,
                int,
            )
            or self.candidate_generation < 1
            or self.candidate_generation >= 2**64
        ):
            raise ValueError(
                "identity candidate generation is invalid"
            )

        started = _parse_timestamp(
            self.invocation_started_at,
            "invocation start timestamp",
        )

        completed = _parse_timestamp(
            self.invocation_completed_at,
            "invocation completion timestamp",
        )

        ingested = _parse_timestamp(
            self.orchestrator_ingested_at,
            "identity ingestion timestamp",
        )

        if completed < started:
            raise ValueError(
                "invocation completion precedes start"
            )

        if ingested < completed:
            raise ValueError(
                "identity ingestion precedes invocation completion"
            )

    def stable_mapping(self) -> dict:
        return {
            "protocol_version":
                self.protocol_version,

            "authenticated_adapter_principal":
                self.authenticated_adapter_principal,

            "authentication_method":
                self.authentication_method,

            "credential_version":
                self.credential_version,

            "provider_principal":
                self.provider_principal,

            "provider_account_scope":
                self.provider_account_scope,

            "requested_model_id":
                self.requested_model_id,

            "requested_endpoint":
                self.requested_endpoint,

            "actual_model_id":
                self.actual_model_id,

            "fallback_model_id":
                self.fallback_model_id,

            "fallback_reason":
                self.fallback_reason,

            "serving_backend":
                self.serving_backend,

            "foundation_model":
                self.foundation_model,

            "foundation_lineage_class":
                self.foundation_lineage_class,

            "foundation_revision":
                self.foundation_revision,

            "hosted_copy_relationship":
                self.hosted_copy_relationship,

            "derivative_relationship":
                self.derivative_relationship,

            "execution_locality":
                self.execution_locality.value,

            "actual_egress_destination":
                self.actual_egress_destination,

            "invocation_id":
                self.invocation_id,

            "execution_receipt_digest":
                self.execution_receipt_digest,

            "request_nonce":
                self.request_nonce,

            "reviewed_material_digest":
                self.reviewed_material_digest,

            "review_request_digest":
                self.review_request_digest,

            "candidate_generation":
                self.candidate_generation,

            "invocation_started_at":
                self.invocation_started_at,

            "invocation_completed_at":
                self.invocation_completed_at,

            "privacy_decision_digest":
                self.privacy_decision_digest,

            "lineage_registry_snapshot_digest":
                self.lineage_registry_snapshot_digest,

            "qualification_registry_snapshot_digest":
                self.qualification_registry_snapshot_digest,

            "qualification_evidence_digest":
                self.qualification_evidence_digest,

            "orchestrator_ingested_at":
                self.orchestrator_ingested_at,

            "authenticated_ingestion_receipt_digest":
                self.authenticated_ingestion_receipt_digest,
        }

    @property
    def identity_envelope_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )

    @property
    def identity_envelope_id(
        self,
    ) -> str:
        return (
            "rei1:"
            + self.identity_envelope_digest
        )

    def validate_for_request(
        self,
        request: ReviewRequestV1,
    ) -> None:
        if (
            self.authenticated_adapter_principal
            != request.required_adapter_principal
        ):
            raise ValueError(
                "identity adapter principal mismatch"
            )

        if (
            self.request_nonce
            != request.request_nonce
        ):
            raise ValueError(
                "identity request nonce mismatch"
            )

        if (
            self.review_request_digest
            != request.request_digest
        ):
            raise ValueError(
                "identity review request digest mismatch"
            )

        if (
            self.reviewed_material_digest
            != request.campaign.reviewed_material_digest
        ):
            raise ValueError(
                "identity reviewed material digest mismatch"
            )

        if (
            self.candidate_generation
            != request.campaign.candidate_generation
        ):
            raise ValueError(
                "identity candidate generation mismatch"
            )

        if (
            self.privacy_decision_digest
            != request.campaign.privacy_decision_digest
        ):
            raise ValueError(
                "identity privacy decision mismatch"
            )

        if (
            self.lineage_registry_snapshot_digest
            != request.campaign.lineage_registry_snapshot_digest
        ):
            raise ValueError(
                "identity lineage registry snapshot mismatch"
            )

        if (
            self.qualification_registry_snapshot_digest
            != request.campaign.qualification_registry_snapshot_digest
        ):
            raise ValueError(
                "identity qualification registry snapshot mismatch"
            )


@dataclass(frozen=True)
class ReviewResultPayloadV1:
    """Canonical immutable result payload.

    The digest is calculated over this object before trusted ingestion.
    Ledger references therefore cannot participate in this digest.
    """

    request: ReviewRequestV1
    reviewer_identity: IdentityEnvelopeV1

    qualification_evidence_digest: str

    invocation_id: str
    execution_nonce: str
    execution_receipt_digest: str

    claimed_verdict: ReviewVerdict
    normalized_findings: tuple[dict, ...]

    invocation_completed_at: str

    raw_result_content_digest: str
    raw_result_storage_ref: str

    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(
                "result protocol version mismatch"
            )

        self.reviewer_identity.validate_for_request(
            self.request
        )

        _require_sha256(
            self.qualification_evidence_digest,
            "result qualification evidence digest",
        )

        if (
            self.qualification_evidence_digest
            != self.reviewer_identity.qualification_evidence_digest
        ):
            raise ValueError(
                "result qualification evidence mismatch"
            )

        _require_identifier(
            self.invocation_id,
            "result invocation id",
        )

        if (
            self.invocation_id
            != self.reviewer_identity.invocation_id
        ):
            raise ValueError(
                "result invocation id mismatch"
            )

        _require_nonce(
            self.execution_nonce
        )

        _require_sha256(
            self.execution_receipt_digest,
            "result execution receipt digest",
        )

        if (
            self.execution_receipt_digest
            != self.reviewer_identity.execution_receipt_digest
        ):
            raise ValueError(
                "result execution receipt mismatch"
            )

        if not isinstance(
            self.claimed_verdict,
            ReviewVerdict,
        ):
            raise ValueError(
                "result verdict is invalid"
            )

        normalized = _normalize_findings(
            self.normalized_findings
        )

        object.__setattr__(
            self,
            "normalized_findings",
            normalized,
        )

        completed = _parse_timestamp(
            self.invocation_completed_at,
            "result invocation completion timestamp",
        )

        identity_completed = _parse_timestamp(
            self.reviewer_identity.invocation_completed_at,
            "identity invocation completion timestamp",
        )

        if completed != identity_completed:
            raise ValueError(
                "result invocation completion mismatch"
            )

        _require_sha256(
            self.raw_result_content_digest,
            "raw result content digest",
        )

        _require_identifier(
            self.raw_result_storage_ref,
            "raw result storage ref",
        )

    @property
    def findings_digest(
        self,
    ) -> str:
        return canonical_digest(
            list(self.normalized_findings)
        )

    def stable_mapping(self) -> dict:
        campaign = self.request.campaign

        return {
            "protocol_version":
                self.protocol_version,

            "review_request_id":
                self.request.review_request_id,

            "request_digest":
                self.request.request_digest,

            "review_campaign_id":
                campaign.review_campaign_id,

            "campaign_context_digest":
                campaign.campaign_context_digest,

            "review_work_unit_id":
                self.request.review_work_unit_id,

            "lane_attempt":
                self.request.lane_attempt,

            "review_round":
                campaign.review_round,

            "candidate_generation":
                campaign.candidate_generation,

            "review_generation":
                campaign.review_generation,

            "objective_sha256":
                campaign.objective_sha256,

            "objective_manifest_hash":
                campaign.objective_manifest_hash,

            "base_sha":
                campaign.base_sha,

            "candidate_sha":
                campaign.candidate_sha,

            "candidate_identity_digest":
                campaign.candidate_identity_digest,

            "candidate_diff_sha256":
                campaign.candidate_diff_sha256,

            "review_scope_manifest_digest":
                campaign.review_scope_manifest_digest,

            "reviewed_material_digest":
                campaign.reviewed_material_digest,

            "local_gate_evidence_digest":
                campaign.local_gate_evidence_digest,

            "policy_revision":
                campaign.policy_revision,

            "policy_decision_digest":
                campaign.policy_decision_digest,

            "risk_decision_digest":
                campaign.risk_decision_digest,

            "privacy_decision_digest":
                campaign.privacy_decision_digest,

            "quorum_policy_digest":
                campaign.quorum_policy_digest,

            "lineage_registry_snapshot_digest":
                campaign.lineage_registry_snapshot_digest,

            "qualification_registry_snapshot_digest":
                campaign.qualification_registry_snapshot_digest,

            "contributor_set_digest":
                campaign.contributor_set_digest,

            "reviewer_identity_envelope_digest":
                self.reviewer_identity.identity_envelope_digest,

            "qualification_evidence_digest":
                self.qualification_evidence_digest,

            "invocation_id":
                self.invocation_id,

            "execution_nonce":
                self.execution_nonce,

            "execution_receipt_digest":
                self.execution_receipt_digest,

            "claimed_verdict":
                self.claimed_verdict.value,

            "normalized_findings":
                list(self.normalized_findings),

            "findings_digest":
                self.findings_digest,

            "invocation_completed_at":
                self.invocation_completed_at,

            "raw_result_content_digest":
                self.raw_result_content_digest,

            "raw_result_storage_ref":
                self.raw_result_storage_ref,
        }

    @property
    def review_result_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )

    @property
    def review_result_id(
        self,
    ) -> str:
        return (
            "rrs1:"
            + self.review_result_digest
        )


@dataclass(frozen=True)
class ResultIngestionV1:
    """Post-result trusted ingestion payload.

    A future ledger record may digest this payload.
    This payload never receives a ledger-record digest back.
    """

    result: ReviewResultPayloadV1

    orchestrator_ingested_at: str
    authenticated_ingestion_receipt_digest: str
    idempotency_key: str

    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(
                "ingestion protocol version mismatch"
            )

        ingested = _parse_timestamp(
            self.orchestrator_ingested_at,
            "result ingestion timestamp",
        )

        completed = _parse_timestamp(
            self.result.invocation_completed_at,
            "result completion timestamp",
        )

        if ingested < completed:
            raise ValueError(
                "result ingestion precedes invocation completion"
            )

        _require_sha256(
            self.authenticated_ingestion_receipt_digest,
            "result ingestion receipt digest",
        )

        _require_identifier(
            self.idempotency_key,
            "result idempotency key",
        )

    def stable_mapping(self) -> dict:
        return {
            "protocol_version":
                self.protocol_version,

            "review_result_id":
                self.result.review_result_id,

            "review_result_digest":
                self.result.review_result_digest,

            "review_request_id":
                self.result.request.review_request_id,

            "request_digest":
                self.result.request.request_digest,

            "reviewer_identity_envelope_digest":
                self.result.reviewer_identity.identity_envelope_digest,

            "invocation_id":
                self.result.invocation_id,

            "execution_receipt_digest":
                self.result.execution_receipt_digest,

            "orchestrator_ingested_at":
                self.orchestrator_ingested_at,

            "authenticated_ingestion_receipt_digest":
                self.authenticated_ingestion_receipt_digest,

            "idempotency_key":
                self.idempotency_key,
        }

    @property
    def ingestion_payload_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )
