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
