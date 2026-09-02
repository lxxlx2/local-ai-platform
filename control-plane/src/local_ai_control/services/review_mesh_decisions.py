"""Deterministic Review Mesh decision logic.

G0-A slice 3 implements:

- append-only contributor-history values
- canonical lineage registry snapshots
- reviewer/contributor lineage independence
- active-result freshness evaluation
- material finding extraction and inheritance

It deliberately performs no provider call, ledger write, quorum
advancement, fixer execution, model execution or runtime mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Mapping

from .review_mesh_protocol import (
    CampaignContextV1,
    IdentityEnvelopeV1,
    ReviewResultPayloadV1,
    canonical_digest,
)


_SHA256 = re.compile(r"[a-f0-9]{64}")
_IDENTIFIER = re.compile(
    r"[A-Za-z0-9_.:/+@-]{1,256}"
)


def _require_sha256(
    value: str,
    label: str,
) -> str:
    value = str(value)

    if not _SHA256.fullmatch(value):
        raise ValueError(
            f"{label} must be a lowercase SHA-256 digest"
        )

    return value


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


def _require_positive_u64(
    value: int,
    label: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value >= 2**64
    ):
        raise ValueError(
            f"{label} must be a positive unsigned 64-bit integer"
        )

    return value


def _require_timestamp(
    value: str,
    label: str,
) -> str:
    try:
        parsed = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError as error:
        raise ValueError(
            f"{label} must be RFC3339/ISO8601"
        ) from error

    if parsed.tzinfo is None:
        raise ValueError(
            f"{label} must be timezone-aware"
        )

    return str(value)


class ContributorRole(str, Enum):
    PRODUCER = "PRODUCER"
    FIXER = "FIXER"


@dataclass(frozen=True)
class ContributorEntryV1:
    role: ContributorRole
    candidate_generation: int

    identity_envelope_digest: str
    invocation_id: str
    execution_receipt_digest: str

    task_or_request_digest: str

    input_candidate_identity_digest: str | None
    output_candidate_identity_digest: str

    contributed_at: str

    def __post_init__(self):
        if not isinstance(
            self.role,
            ContributorRole,
        ):
            raise ValueError(
                "contributor role is invalid"
            )

        _require_positive_u64(
            self.candidate_generation,
            "contributor candidate generation",
        )

        for label, value in (
            (
                "contributor identity envelope digest",
                self.identity_envelope_digest,
            ),
            (
                "contributor execution receipt digest",
                self.execution_receipt_digest,
            ),
            (
                "contributor task/request digest",
                self.task_or_request_digest,
            ),
            (
                "output candidate identity digest",
                self.output_candidate_identity_digest,
            ),
        ):
            _require_sha256(
                value,
                label,
            )

        if (
            self.input_candidate_identity_digest
            is not None
        ):
            _require_sha256(
                self.input_candidate_identity_digest,
                "input candidate identity digest",
            )

        _require_identifier(
            self.invocation_id,
            "contributor invocation id",
        )

        _require_timestamp(
            self.contributed_at,
            "contributor timestamp",
        )

    def stable_mapping(self) -> dict:
        return {
            "role":
                self.role.value,

            "candidate_generation":
                self.candidate_generation,

            "identity_envelope_digest":
                self.identity_envelope_digest,

            "invocation_id":
                self.invocation_id,

            "execution_receipt_digest":
                self.execution_receipt_digest,

            "task_or_request_digest":
                self.task_or_request_digest,

            "input_candidate_identity_digest":
                self.input_candidate_identity_digest,

            "output_candidate_identity_digest":
                self.output_candidate_identity_digest,

            "contributed_at":
                self.contributed_at,
        }


@dataclass(frozen=True)
class ContributorHistoryV1:
    entries: tuple[ContributorEntryV1, ...]

    def __post_init__(self):
        if not self.entries:
            raise ValueError(
                "contributor history must contain a producer"
            )

        first = self.entries[0]

        if first.role is not ContributorRole.PRODUCER:
            raise ValueError(
                "first contributor must be PRODUCER"
            )

        if (
            first.input_candidate_identity_digest
            is not None
        ):
            raise ValueError(
                "first producer input candidate must be NONE"
            )

        seen_publications = set()

        for index, entry in enumerate(
            self.entries
        ):
            publication = (
                entry.candidate_generation,
                entry.invocation_id,
                entry.execution_receipt_digest,
            )

            if publication in seen_publications:
                raise ValueError(
                    "duplicate contributor publication"
                )

            seen_publications.add(
                publication
            )

            if index == 0:
                continue

            previous = self.entries[
                index - 1
            ]

            if (
                entry.candidate_generation
                <= previous.candidate_generation
            ):
                raise ValueError(
                    "contributor generations must strictly increase"
                )

            if (
                entry.input_candidate_identity_digest
                != previous.output_candidate_identity_digest
            ):
                raise ValueError(
                    "contributor candidate chain is discontinuous"
                )

    def stable_mapping(self) -> dict:
        return {
            "entries": [
                entry.stable_mapping()
                for entry in self.entries
            ]
        }

    @property
    def contributor_set_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )

    @property
    def latest_candidate_identity_digest(
        self,
    ) -> str:
        return (
            self.entries[-1]
            .output_candidate_identity_digest
        )

    def append(
        self,
        entry: ContributorEntryV1,
    ) -> "ContributorHistoryV1":
        return ContributorHistoryV1(
            self.entries + (
                entry,
            )
        )


class LineageApprovalState(str, Enum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"


@dataclass(frozen=True)
class LineageRegistryEntryV1:
    provider_principal: str
    serving_backend: str
    actual_model_id: str

    foundation_model: str
    foundation_revision: str
    foundation_lineage_class: str

    correlation_group: str
    approval_state: LineageApprovalState

    def __post_init__(self):
        for label, value in (
            (
                "lineage provider principal",
                self.provider_principal,
            ),
            (
                "lineage serving backend",
                self.serving_backend,
            ),
            (
                "lineage actual model id",
                self.actual_model_id,
            ),
            (
                "lineage foundation model",
                self.foundation_model,
            ),
            (
                "lineage foundation revision",
                self.foundation_revision,
            ),
            (
                "foundation lineage class",
                self.foundation_lineage_class,
            ),
            (
                "lineage correlation group",
                self.correlation_group,
            ),
        ):
            _require_identifier(
                value,
                label,
            )

        if not isinstance(
            self.approval_state,
            LineageApprovalState,
        ):
            raise ValueError(
                "lineage approval state is invalid"
            )

    @property
    def lookup_key(
        self,
    ) -> tuple[str, str, str]:
        return (
            self.provider_principal,
            self.serving_backend,
            self.actual_model_id,
        )

    def stable_mapping(self) -> dict:
        return {
            "provider_principal":
                self.provider_principal,

            "serving_backend":
                self.serving_backend,

            "actual_model_id":
                self.actual_model_id,

            "foundation_model":
                self.foundation_model,

            "foundation_revision":
                self.foundation_revision,

            "foundation_lineage_class":
                self.foundation_lineage_class,

            "correlation_group":
                self.correlation_group,

            "approval_state":
                self.approval_state.value,
        }


@dataclass(frozen=True)
class LineageRegistrySnapshotV1:
    policy_revision: str
    entries: tuple[
        LineageRegistryEntryV1,
        ...
    ]
    # G0-B compiles this quorum view from a richer, authoritative registry.
    # Campaigns must bind the authoritative registry digest, not a lossy view
    # digest.  Legacy/direct G0-A snapshots leave this unset.
    source_registry_snapshot_digest: str | None = None

    def __post_init__(self):
        _require_identifier(
            self.policy_revision,
            "lineage policy revision",
        )

        if self.source_registry_snapshot_digest is not None:
            _require_sha256(
                self.source_registry_snapshot_digest,
                "source lineage registry snapshot digest",
            )

        seen = {}

        for entry in self.entries:
            key = entry.lookup_key

            if key in seen:
                raise ValueError(
                    "duplicate lineage registry identity"
                )

            seen[key] = entry

    def stable_mapping(self) -> dict:
        ordered = sorted(
            (
                entry.stable_mapping()
                for entry in self.entries
            ),
            key=lambda item: (
                item["provider_principal"],
                item["serving_backend"],
                item["actual_model_id"],
            ),
        )

        return {
            "policy_revision":
                self.policy_revision,

            "entries":
                ordered,
        }

    @property
    def snapshot_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )

    @property
    def binding_digest(self) -> str:
        """Digest that protocol/campaign objects must bind."""

        return self.source_registry_snapshot_digest or self.snapshot_digest

    def resolve(
        self,
        facts: "ObservedIdentityFactsV1",
    ) -> LineageRegistryEntryV1 | None:
        if (
            facts.lineage_registry_snapshot_digest
            != self.binding_digest
        ):
            return None

        matches = [
            entry
            for entry in self.entries
            if entry.lookup_key
            == facts.lookup_key
        ]

        if len(matches) != 1:
            return None

        entry = matches[0]

        if (
            entry.approval_state
            is not LineageApprovalState.APPROVED
        ):
            return None

        if (
            entry.foundation_model
            != facts.foundation_model
            or entry.foundation_revision
            != facts.foundation_revision
            or entry.foundation_lineage_class
            != facts.claimed_foundation_lineage_class
        ):
            return None

        return entry


@dataclass(frozen=True)
class ObservedIdentityFactsV1:
    identity_envelope_digest: str

    provider_principal: str
    serving_backend: str
    actual_model_id: str

    foundation_model: str
    foundation_revision: str
    claimed_foundation_lineage_class: str

    lineage_registry_snapshot_digest: str

    def __post_init__(self):
        _require_sha256(
            self.identity_envelope_digest,
            "observed identity envelope digest",
        )

        _require_sha256(
            self.lineage_registry_snapshot_digest,
            "observed lineage registry snapshot digest",
        )

        for label, value in (
            (
                "observed provider principal",
                self.provider_principal,
            ),
            (
                "observed serving backend",
                self.serving_backend,
            ),
            (
                "observed actual model id",
                self.actual_model_id,
            ),
            (
                "observed foundation model",
                self.foundation_model,
            ),
            (
                "observed foundation revision",
                self.foundation_revision,
            ),
            (
                "observed foundation lineage class",
                self.claimed_foundation_lineage_class,
            ),
        ):
            _require_identifier(
                value,
                label,
            )

    @property
    def lookup_key(
        self,
    ) -> tuple[str, str, str]:
        return (
            self.provider_principal,
            self.serving_backend,
            self.actual_model_id,
        )

    @classmethod
    def from_review_identity(
        cls,
        identity: IdentityEnvelopeV1,
    ) -> "ObservedIdentityFactsV1":
        return cls(
            identity_envelope_digest=(
                identity.identity_envelope_digest
            ),

            provider_principal=(
                identity.provider_principal
            ),

            serving_backend=(
                identity.serving_backend
            ),

            actual_model_id=(
                identity.actual_model_id
            ),

            foundation_model=(
                identity.foundation_model
            ),

            foundation_revision=(
                identity.foundation_revision
            ),

            claimed_foundation_lineage_class=(
                identity.foundation_lineage_class
            ),

            lineage_registry_snapshot_digest=(
                identity
                .lineage_registry_snapshot_digest
            ),
        )


class IndependenceStatus(str, Enum):
    INDEPENDENT = "INDEPENDENT"
    NON_INDEPENDENT = "NON_INDEPENDENT"
    BLOCKED_IDENTITY_RECONCILIATION = (
        "BLOCKED_IDENTITY_RECONCILIATION"
    )


@dataclass(frozen=True)
class IndependenceDecisionV1:
    status: IndependenceStatus
    reason: str
    reviewer_lineage_class: str | None

    def __post_init__(self):
        if not isinstance(
            self.status,
            IndependenceStatus,
        ):
            raise ValueError(
                "independence status is invalid"
            )

        _require_identifier(
            self.reason,
            "independence reason",
        )

        if (
            self.reviewer_lineage_class
            is not None
        ):
            _require_identifier(
                self.reviewer_lineage_class,
                "reviewer lineage class",
            )


def evaluate_independence(
    *,
    reviewer: ObservedIdentityFactsV1,
    contributor_history: ContributorHistoryV1,
    contributor_identities: Mapping[
        str,
        ObservedIdentityFactsV1,
    ],
    lineage_registry: LineageRegistrySnapshotV1,
    other_counted_reviewers: tuple[
        ObservedIdentityFactsV1,
        ...
    ] = (),
) -> IndependenceDecisionV1:
    reviewer_entry = (
        lineage_registry.resolve(
            reviewer
        )
    )

    if reviewer_entry is None:
        return IndependenceDecisionV1(
            IndependenceStatus.NON_INDEPENDENT,
            "reviewer-lineage-unknown-or-unapproved",
            None,
        )

    reviewer_class = (
        reviewer_entry.foundation_lineage_class
    )

    for contributor in (
        contributor_history.entries
    ):
        facts = contributor_identities.get(
            contributor.identity_envelope_digest
        )

        if facts is None:
            return IndependenceDecisionV1(
                IndependenceStatus
                .BLOCKED_IDENTITY_RECONCILIATION,
                "contributor-identity-facts-missing",
                reviewer_class,
            )

        if (
            facts.identity_envelope_digest
            != contributor.identity_envelope_digest
        ):
            return IndependenceDecisionV1(
                IndependenceStatus
                .BLOCKED_IDENTITY_RECONCILIATION,
                "contributor-identity-digest-mismatch",
                reviewer_class,
            )

        contributor_entry = (
            lineage_registry.resolve(
                facts
            )
        )

        if contributor_entry is None:
            return IndependenceDecisionV1(
                IndependenceStatus
                .BLOCKED_IDENTITY_RECONCILIATION,
                "contributor-lineage-unresolved",
                reviewer_class,
            )

        if (
            contributor_entry
            .foundation_lineage_class
            == reviewer_class
        ):
            return IndependenceDecisionV1(
                IndependenceStatus.NON_INDEPENDENT,
                "reviewer-shares-contributor-lineage",
                reviewer_class,
            )

        if (
            reviewer_entry.correlation_group
            != "NONE"
            and reviewer_entry.correlation_group
            == contributor_entry.correlation_group
        ):
            return IndependenceDecisionV1(
                IndependenceStatus.NON_INDEPENDENT,
                "reviewer-shares-contributor-correlation-group",
                reviewer_class,
            )

    for other in other_counted_reviewers:
        other_entry = (
            lineage_registry.resolve(
                other
            )
        )

        if other_entry is None:
            return IndependenceDecisionV1(
                IndependenceStatus.NON_INDEPENDENT,
                "other-reviewer-lineage-unresolved",
                reviewer_class,
            )

        if (
            other_entry.foundation_lineage_class
            == reviewer_class
        ):
            return IndependenceDecisionV1(
                IndependenceStatus.NON_INDEPENDENT,
                "reviewer-duplicates-counted-lineage",
                reviewer_class,
            )

        if (
            reviewer_entry.correlation_group
            != "NONE"
            and reviewer_entry.correlation_group
            == other_entry.correlation_group
        ):
            return IndependenceDecisionV1(
                IndependenceStatus.NON_INDEPENDENT,
                "reviewer-duplicates-counted-correlation-group",
                reviewer_class,
            )

    return IndependenceDecisionV1(
        IndependenceStatus.INDEPENDENT,
        "independent-lineage-confirmed",
        reviewer_class,
    )


class FreshnessStatus(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    INVALID = "INVALID"
    BLOCKED_IDENTITY_RECONCILIATION = (
        "BLOCKED_IDENTITY_RECONCILIATION"
    )


@dataclass(frozen=True)
class ActiveReviewBindingsV1:
    campaign: CampaignContextV1

    contributor_history: ContributorHistoryV1
    lineage_registry: LineageRegistrySnapshotV1

    qualification_registry_snapshot_digest: str

    revoked_result_digests: frozenset[str] = (
        frozenset()
    )

    revoked_identity_envelope_digests: (
        frozenset[str]
    ) = frozenset()

    def __post_init__(self):
        _require_sha256(
            self.qualification_registry_snapshot_digest,
            "active qualification registry snapshot digest",
        )

        for value in (
            self.revoked_result_digests
        ):
            _require_sha256(
                value,
                "revoked result digest",
            )

        for value in (
            self.revoked_identity_envelope_digests
        ):
            _require_sha256(
                value,
                "revoked identity digest",
            )


@dataclass(frozen=True)
class FreshnessDecisionV1:
    status: FreshnessStatus
    reason: str

    def __post_init__(self):
        if not isinstance(
            self.status,
            FreshnessStatus,
        ):
            raise ValueError(
                "freshness status is invalid"
            )

        _require_identifier(
            self.reason,
            "freshness reason",
        )


def evaluate_result_freshness(
    result: ReviewResultPayloadV1,
    active: ActiveReviewBindingsV1,
) -> FreshnessDecisionV1:
    if (
        result.review_result_digest
        in active.revoked_result_digests
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.INVALID,
            "result-revoked",
        )

    identity_digest = (
        result.reviewer_identity
        .identity_envelope_digest
    )

    if (
        identity_digest
        in active
        .revoked_identity_envelope_digests
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.INVALID,
            "reviewer-identity-revoked",
        )

    if (
        active.contributor_history
        .contributor_set_digest
        != active.campaign
        .contributor_set_digest
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.STALE,
            "active-contributor-history-changed",
        )

    if (
        active.lineage_registry
        .binding_digest
        != active.campaign
        .lineage_registry_snapshot_digest
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.STALE,
            "active-lineage-registry-changed",
        )

    if (
        active
        .qualification_registry_snapshot_digest
        != active.campaign
        .qualification_registry_snapshot_digest
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.STALE,
            "active-qualification-registry-changed",
        )

    result_campaign = (
        result.request.campaign
    )

    if (
        result_campaign
        .campaign_context_digest
        != active.campaign
        .campaign_context_digest
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.STALE,
            "campaign-context-mismatch",
        )

    if (
        result_campaign
        .review_campaign_id
        != active.campaign
        .review_campaign_id
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.STALE,
            "campaign-id-mismatch",
        )

    if (
        result_campaign
        .candidate_generation
        != active.campaign
        .candidate_generation
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.STALE,
            "candidate-generation-mismatch",
        )

    if (
        result_campaign
        .review_generation
        != active.campaign
        .review_generation
    ):
        return FreshnessDecisionV1(
            FreshnessStatus.STALE,
            "review-generation-mismatch",
        )

    return FreshnessDecisionV1(
        FreshnessStatus.CURRENT,
        "all-active-bindings-current",
    )


class FindingState(str, Enum):
    OPEN = "OPEN"
    REPAIR_PROPOSED = "REPAIR_PROPOSED"
    VERIFIED_CLOSED = "VERIFIED_CLOSED"
    DISMISSED = "DISMISSED"
    REOPENED = "REOPENED"


_UNRESOLVED_FINDING_STATES = frozenset({
    FindingState.OPEN,
    FindingState.REPAIR_PROPOSED,
    FindingState.REOPENED,
})

_MATERIAL_SEVERITIES = frozenset({
    "BLOCKING",
    "HIGH",
})


@dataclass(frozen=True)
class MaterialFindingV1:
    finding_id: str

    originating_result_digest: str
    finding_ordinal: int
    finding_content_digest: str

    candidate_generation_introduced: int

    scope: str
    severity: str
    file: str | None
    evidence: str
    recommended_fix: str

    state: FindingState = FindingState.OPEN

    def __post_init__(self):
        _require_identifier(
            self.finding_id,
            "material finding id",
        )

        _require_sha256(
            self.originating_result_digest,
            "originating result digest",
        )

        _require_sha256(
            self.finding_content_digest,
            "finding content digest",
        )

        _require_positive_u64(
            self.candidate_generation_introduced,
            "finding candidate generation",
        )

        if (
            isinstance(
                self.finding_ordinal,
                bool,
            )
            or not isinstance(
                self.finding_ordinal,
                int,
            )
            or self.finding_ordinal < 1
        ):
            raise ValueError(
                "finding ordinal is invalid"
            )

        if (
            self.severity
            not in _MATERIAL_SEVERITIES
        ):
            raise ValueError(
                "material finding severity is invalid"
            )

        if not isinstance(
            self.state,
            FindingState,
        ):
            raise ValueError(
                "finding state is invalid"
            )

        expected = (
            self.compute_finding_id(
                originating_result_digest=(
                    self.originating_result_digest
                ),
                finding_ordinal=(
                    self.finding_ordinal
                ),
                finding_content_digest=(
                    self.finding_content_digest
                ),
            )
        )

        if self.finding_id != expected:
            raise ValueError(
                "material finding id mismatch"
            )

    @staticmethod
    def compute_finding_id(
        *,
        originating_result_digest: str,
        finding_ordinal: int,
        finding_content_digest: str,
    ) -> str:
        digest = canonical_digest({
            "originating_result_digest":
                originating_result_digest,

            "finding_ordinal":
                finding_ordinal,

            "finding_content_digest":
                finding_content_digest,
        })

        return (
            "mf1:"
            + digest
        )

    @property
    def unresolved(
        self,
    ) -> bool:
        return (
            self.state
            in _UNRESOLVED_FINDING_STATES
        )

    def stable_mapping(self) -> dict:
        return {
            "finding_id":
                self.finding_id,

            "originating_result_digest":
                self.originating_result_digest,

            "finding_ordinal":
                self.finding_ordinal,

            "finding_content_digest":
                self.finding_content_digest,

            "candidate_generation_introduced":
                self.candidate_generation_introduced,

            "scope":
                self.scope,

            "severity":
                self.severity,

            "file":
                self.file,

            "evidence":
                self.evidence,

            "recommended_fix":
                self.recommended_fix,

            "state":
                self.state.value,
        }


def material_findings_from_result(
    result: ReviewResultPayloadV1,
) -> tuple[MaterialFindingV1, ...]:
    material = []

    for ordinal, finding in enumerate(
        result.normalized_findings,
        start=1,
    ):
        if (
            finding["severity"]
            not in _MATERIAL_SEVERITIES
        ):
            continue

        content_digest = canonical_digest(
            finding
        )

        finding_id = (
            MaterialFindingV1
            .compute_finding_id(
                originating_result_digest=(
                    result.review_result_digest
                ),
                finding_ordinal=ordinal,
                finding_content_digest=(
                    content_digest
                ),
            )
        )

        material.append(
            MaterialFindingV1(
                finding_id=finding_id,

                originating_result_digest=(
                    result.review_result_digest
                ),

                finding_ordinal=ordinal,

                finding_content_digest=(
                    content_digest
                ),

                candidate_generation_introduced=(
                    result.request.campaign
                    .candidate_generation
                ),

                scope=finding["scope"],
                severity=finding["severity"],
                file=finding["file"],
                evidence=finding["evidence"],
                recommended_fix=(
                    finding["recommended_fix"]
                ),

                state=FindingState.OPEN,
            )
        )

    return tuple(material)


@dataclass(frozen=True)
class InheritedFindingSetV1:
    findings: tuple[
        MaterialFindingV1,
        ...
    ] = ()

    def __post_init__(self):
        seen = {}

        for finding in self.findings:
            previous = seen.get(
                finding.finding_id
            )

            if previous is not None:
                if previous != finding:
                    raise ValueError(
                        "conflicting duplicate material finding"
                    )

                raise ValueError(
                    "duplicate material finding"
                )

            seen[
                finding.finding_id
            ] = finding

    @property
    def unresolved(
        self,
    ) -> tuple[
        MaterialFindingV1,
        ...
    ]:
        return tuple(
            finding
            for finding in self.findings
            if finding.unresolved
        )

    @property
    def finding_set_digest(
        self,
    ) -> str:
        ordered = sorted(
            (
                finding.stable_mapping()
                for finding in self.findings
            ),
            key=lambda item: (
                item["finding_id"]
            ),
        )

        return canonical_digest({
            "findings": ordered,
        })

    def merge_result(
        self,
        result: ReviewResultPayloadV1,
    ) -> "InheritedFindingSetV1":
        current = {
            finding.finding_id:
                finding
            for finding in self.findings
        }

        for finding in (
            material_findings_from_result(
                result
            )
        ):
            existing = current.get(
                finding.finding_id
            )

            if (
                existing is not None
                and existing != finding
            ):
                raise ValueError(
                    "material finding identity collision"
                )

            current.setdefault(
                finding.finding_id,
                finding,
            )

        ordered = tuple(
            current[key]
            for key in sorted(current)
        )

        return InheritedFindingSetV1(
            ordered
        )
