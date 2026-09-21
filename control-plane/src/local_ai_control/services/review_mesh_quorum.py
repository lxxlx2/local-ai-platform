"""Deterministic Review Mesh quorum and Owner-gate evaluator.

G0-A Slice 6 performs no model/provider/network/merge/deploy action.

It consumes already trusted protocol/evidence objects and computes:

- reviewer qualification eligibility
- current/non-current vote eligibility
- contributor/reviewer lineage independence
- duplicate invocation/result rejection
- P0/P1/P2 quorum floors
- WAITING_FOR_INDEPENDENT_REVIEW
- OWNER_GATE_READY

OWNER_GATE_READY is evidence state only. It never authorizes a protected
action such as merge, deploy, service restart, activation, paid usage or
privilege expansion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping

from .review_mesh_protocol import (
    PROTOCOL_VERSION,
    ReviewerClass,
    ReviewResultPayloadV1,
    ReviewVerdict,
    RiskLevel,
    canonical_digest,
)

from .review_mesh_decisions import (
    ActiveReviewBindingsV1,
    ContributorHistoryV1,
    FreshnessStatus,
    IndependenceStatus,
    InheritedFindingSetV1,
    LineageRegistrySnapshotV1,
    ObservedIdentityFactsV1,
    evaluate_independence,
    evaluate_result_freshness,
)


_SHA256 = re.compile(r"[a-f0-9]{64}")
_IDENTIFIER = re.compile(
    r"[A-Za-z0-9_.:/+@-]{1,256}"
)


def _sha256(
    value: str,
    label: str,
) -> str:
    value = str(value)

    if not _SHA256.fullmatch(value):
        raise ValueError(
            f"{label} must be lowercase SHA-256"
        )

    return value


def _identifier(
    value: str,
    label: str,
) -> str:
    value = str(value)

    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{label} is invalid"
        )

    return value


class TrustedCheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ReviewerCapacityState(str, Enum):
    AVAILABLE = "AVAILABLE"
    TEMPORARILY_UNAVAILABLE = (
        "TEMPORARILY_UNAVAILABLE"
    )
    UNKNOWN = "UNKNOWN"


class ReviewMeshDecisionState(str, Enum):
    INVALID_REVIEW_REQUEST = (
        "INVALID_REVIEW_REQUEST"
    )

    UNTRUSTED_RESULT = (
        "UNTRUSTED_RESULT"
    )

    UNVERIFIED_IDENTITY = (
        "UNVERIFIED_IDENTITY"
    )

    NON_INDEPENDENT = (
        "NON_INDEPENDENT"
    )

    STALE = "STALE"

    INVALID_POLICY_DOWNGRADE = (
        "INVALID_POLICY_DOWNGRADE"
    )

    WAITING_FOR_INDEPENDENT_REVIEW = (
        "WAITING_FOR_INDEPENDENT_REVIEW"
    )

    REVIEW_IN_PROGRESS = (
        "REVIEW_IN_PROGRESS"
    )

    BLOCKED_IDENTITY_RECONCILIATION = (
        "BLOCKED_IDENTITY_RECONCILIATION"
    )

    BLOCKED_LEDGER_RECONCILIATION = (
        "BLOCKED_LEDGER_RECONCILIATION"
    )

    BLOCKED_PRIVACY_RECONCILIATION = (
        "BLOCKED_PRIVACY_RECONCILIATION"
    )

    BLOCKED_REVIEW_CAPACITY_RECONCILIATION = (
        "BLOCKED_REVIEW_CAPACITY_RECONCILIATION"
    )

    BLOCKED_DETERMINISTIC_GATE = (
        "BLOCKED_DETERMINISTIC_GATE"
    )

    BLOCKED_SECURITY_OR_PRIVACY = (
        "BLOCKED_SECURITY_OR_PRIVACY"
    )

    BLOCKED_MATERIAL_FINDING = (
        "BLOCKED_MATERIAL_FINDING"
    )

    BLOCKED_FIXER_CONVERGENCE = (
        "BLOCKED_FIXER_CONVERGENCE"
    )

    OWNER_GATE_READY = (
        "OWNER_GATE_READY"
    )


_REVIEWER_CLASS_RANK = {
    ReviewerClass.P3: 0,
    ReviewerClass.P2: 1,
    ReviewerClass.STRONG_P1: 2,
    ReviewerClass.STRONG_P0: 3,
}


@dataclass(frozen=True)
class QuorumPolicyV1:
    policy_revision: str
    risk_level: RiskLevel

    required_reviewer_class: ReviewerClass
    minimum_independent_families: int

    deterministic_gates_required: bool = True
    security_evidence_required: bool = True
    privacy_evidence_required: bool = True

    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        _identifier(
            self.policy_revision,
            "quorum policy revision",
        )

        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(
                "quorum protocol version mismatch"
            )

        if not isinstance(
            self.risk_level,
            RiskLevel,
        ):
            raise ValueError(
                "quorum risk level is invalid"
            )

        if not isinstance(
            self.required_reviewer_class,
            ReviewerClass,
        ):
            raise ValueError(
                "required reviewer class is invalid"
            )

        if (
            isinstance(
                self.minimum_independent_families,
                bool,
            )
            or not isinstance(
                self.minimum_independent_families,
                int,
            )
            or self.minimum_independent_families < 0
            or self.minimum_independent_families > 32
        ):
            raise ValueError(
                "minimum independent family count is invalid"
            )

    def stable_mapping(self) -> dict:
        return {
            "protocol_version":
                self.protocol_version,

            "policy_revision":
                self.policy_revision,

            "risk_level":
                self.risk_level.value,

            "required_reviewer_class":
                self.required_reviewer_class.value,

            "minimum_independent_families":
                self.minimum_independent_families,

            "deterministic_gates_required":
                self.deterministic_gates_required,

            "security_evidence_required":
                self.security_evidence_required,

            "privacy_evidence_required":
                self.privacy_evidence_required,
        }

    @property
    def policy_digest(self) -> str:
        return canonical_digest(
            self.stable_mapping()
        )

    def satisfies_v1_floor(self) -> bool:
        required_rank = (
            _REVIEWER_CLASS_RANK[
                self.required_reviewer_class
            ]
        )

        if self.risk_level is RiskLevel.P0:
            return (
                self.minimum_independent_families >= 2
                and required_rank
                >= _REVIEWER_CLASS_RANK[
                    ReviewerClass.STRONG_P0
                ]
            )

        if self.risk_level is RiskLevel.P1:
            return (
                self.minimum_independent_families >= 2
                and required_rank
                >= _REVIEWER_CLASS_RANK[
                    ReviewerClass.STRONG_P1
                ]
            )

        if self.risk_level is RiskLevel.P2:
            return (
                self.minimum_independent_families >= 1
                and required_rank
                >= _REVIEWER_CLASS_RANK[
                    ReviewerClass.P2
                ]
            )

        # P3 is repository/subsystem-policy specific.
        return True


@dataclass(frozen=True)
class QualificationEligibilityV1:
    """Pre-existing qualification evidence used for one actual identity.

    This object deliberately does not contain the current invocation's
    identity-envelope digest, avoiding a qualification <-> identity digest
    cycle. The current IdentityEnvelope instead references the immutable
    qualification_evidence_digest.
    """

    qualification_evidence_digest: str

    actual_model_id: str
    foundation_lineage_class: str

    qualified_reviewer_class: ReviewerClass
    eligible_risk_levels: tuple[
        RiskLevel,
        ...
    ]

    protocol_revision: str
    benchmark_harness_policy_revision: str

    qualification_registry_snapshot_digest: str

    active: bool = True

    def __post_init__(self):
        _sha256(
            self.qualification_evidence_digest,
            "qualification evidence digest",
        )

        _identifier(
            self.actual_model_id,
            "qualified actual model id",
        )

        _identifier(
            self.foundation_lineage_class,
            "qualified foundation lineage",
        )

        if not isinstance(
            self.qualified_reviewer_class,
            ReviewerClass,
        ):
            raise ValueError(
                "qualified reviewer class is invalid"
            )

        if not self.eligible_risk_levels:
            raise ValueError(
                "qualification has no eligible risk levels"
            )

        for level in self.eligible_risk_levels:
            if not isinstance(
                level,
                RiskLevel,
            ):
                raise ValueError(
                    "qualification risk level is invalid"
                )

        if (
            len(set(self.eligible_risk_levels))
            != len(self.eligible_risk_levels)
        ):
            raise ValueError(
                "duplicate qualification risk level"
            )

        _identifier(
            self.protocol_revision,
            "qualification protocol revision",
        )

        _identifier(
            self.benchmark_harness_policy_revision,
            "qualification benchmark revision",
        )

        _sha256(
            self.qualification_registry_snapshot_digest,
            "qualification registry snapshot digest",
        )

    def covers(
        self,
        *,
        result: ReviewResultPayloadV1,
        policy: QuorumPolicyV1,
    ) -> bool:
        identity = (
            result.reviewer_identity
        )

        if not self.active:
            return False

        if (
            identity.qualification_evidence_digest
            != self.qualification_evidence_digest
        ):
            return False

        if (
            identity.actual_model_id
            != self.actual_model_id
        ):
            return False

        if (
            identity.foundation_lineage_class
            != self.foundation_lineage_class
        ):
            return False

        if (
            identity
            .qualification_registry_snapshot_digest
            != self
            .qualification_registry_snapshot_digest
        ):
            return False

        if (
            result.request.campaign
            .qualification_registry_snapshot_digest
            != self
            .qualification_registry_snapshot_digest
        ):
            return False

        if (
            self.protocol_revision
            != PROTOCOL_VERSION
        ):
            return False

        if (
            self.benchmark_harness_policy_revision
            != result.request.campaign
            .benchmark_harness_policy_revision
        ):
            return False

        if (
            policy.risk_level
            not in self.eligible_risk_levels
        ):
            return False

        return (
            _REVIEWER_CLASS_RANK[
                self.qualified_reviewer_class
            ]
            >=
            _REVIEWER_CLASS_RANK[
                policy.required_reviewer_class
            ]
        )


@dataclass(frozen=True)
class TrustedOwnerGateInputsV1:
    ledger_continuity: TrustedCheckStatus
    contributor_provenance: TrustedCheckStatus

    deterministic_gates: TrustedCheckStatus
    security_evidence: TrustedCheckStatus
    privacy_evidence: TrustedCheckStatus

    fixer_convergence_clear: TrustedCheckStatus

    reviewer_capacity: ReviewerCapacityState

    def __post_init__(self):
        for value in (
            self.ledger_continuity,
            self.contributor_provenance,
            self.deterministic_gates,
            self.security_evidence,
            self.privacy_evidence,
            self.fixer_convergence_clear,
        ):
            if not isinstance(
                value,
                TrustedCheckStatus,
            ):
                raise ValueError(
                    "trusted gate status is invalid"
                )

        if not isinstance(
            self.reviewer_capacity,
            ReviewerCapacityState,
        ):
            raise ValueError(
                "reviewer capacity state is invalid"
            )


@dataclass(frozen=True)
class VoteRejectionV1:
    result_digest: str
    reason: str

    def __post_init__(self):
        _sha256(
            self.result_digest,
            "rejected result digest",
        )

        _identifier(
            self.reason,
            "vote rejection reason",
        )


@dataclass(frozen=True)
class QuorumDecisionV1:
    state: ReviewMeshDecisionState

    required_independent_families: int

    counted_result_digests: tuple[
        str,
        ...
    ]

    counted_lineage_classes: tuple[
        str,
        ...
    ]

    rejected_votes: tuple[
        VoteRejectionV1,
        ...
    ]

    reason: str

    def __post_init__(self):
        if not isinstance(
            self.state,
            ReviewMeshDecisionState,
        ):
            raise ValueError(
                "quorum state is invalid"
            )

        _identifier(
            self.reason,
            "quorum reason",
        )

        for digest in self.counted_result_digests:
            _sha256(
                digest,
                "counted result digest",
            )

        for lineage in self.counted_lineage_classes:
            _identifier(
                lineage,
                "counted lineage class",
            )

        if (
            len(set(self.counted_result_digests))
            != len(self.counted_result_digests)
        ):
            raise ValueError(
                "duplicate counted result digest"
            )

        if (
            len(set(self.counted_lineage_classes))
            != len(self.counted_lineage_classes)
        ):
            raise ValueError(
                "duplicate counted lineage class"
            )

    @property
    def protected_action_authorized(
        self,
    ) -> bool:
        # OWNER_GATE_READY is intentionally not action authorization.
        return False

    def stable_mapping(self) -> dict:
        return {
            "state":
                self.state.value,

            "required_independent_families":
                self.required_independent_families,

            "counted_result_digests":
                list(
                    self.counted_result_digests
                ),

            "counted_lineage_classes":
                list(
                    self.counted_lineage_classes
                ),

            "rejected_votes": [
                {
                    "result_digest":
                        item.result_digest,

                    "reason":
                        item.reason,
                }
                for item in self.rejected_votes
            ],

            "reason":
                self.reason,

            "protected_action_authorized":
                False,
        }

    @property
    def decision_digest(self) -> str:
        return canonical_digest(
            self.stable_mapping()
        )


def _terminal_prerequisite_decision(
    *,
    policy: QuorumPolicyV1,
    campaign,
    trusted: TrustedOwnerGateInputsV1,
    inherited_findings: InheritedFindingSetV1,
) -> QuorumDecisionV1 | None:
    empty = ()

    if (
        policy.policy_digest
        != campaign.quorum_policy_digest
        or policy.risk_level
        is not campaign.risk_level
        or policy.required_reviewer_class
        is not campaign.required_reviewer_class
        or not policy.satisfies_v1_floor()
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .INVALID_POLICY_DOWNGRADE,
            policy.minimum_independent_families,
            empty,
            empty,
            empty,
            "quorum-policy-binding-or-floor-invalid",
        )

    if (
        trusted.ledger_continuity
        is not TrustedCheckStatus.PASS
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .BLOCKED_LEDGER_RECONCILIATION,
            policy.minimum_independent_families,
            empty,
            empty,
            empty,
            "ledger-continuity-not-verified",
        )

    if (
        trusted.contributor_provenance
        is not TrustedCheckStatus.PASS
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .BLOCKED_IDENTITY_RECONCILIATION,
            policy.minimum_independent_families,
            empty,
            empty,
            empty,
            "contributor-provenance-not-verified",
        )

    if (
        policy.deterministic_gates_required
        and trusted.deterministic_gates
        is not TrustedCheckStatus.PASS
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .BLOCKED_DETERMINISTIC_GATE,
            policy.minimum_independent_families,
            empty,
            empty,
            empty,
            "deterministic-gates-not-passing",
        )

    if (
        policy.security_evidence_required
        and trusted.security_evidence
        is not TrustedCheckStatus.PASS
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .BLOCKED_SECURITY_OR_PRIVACY,
            policy.minimum_independent_families,
            empty,
            empty,
            empty,
            "security-evidence-not-passing",
        )

    if (
        policy.privacy_evidence_required
    ):
        if (
            trusted.privacy_evidence
            is TrustedCheckStatus.UNKNOWN
        ):
            return QuorumDecisionV1(
                ReviewMeshDecisionState
                .BLOCKED_PRIVACY_RECONCILIATION,
                policy.minimum_independent_families,
                empty,
                empty,
                empty,
                "privacy-evidence-ambiguous",
            )

        if (
            trusted.privacy_evidence
            is TrustedCheckStatus.FAIL
        ):
            return QuorumDecisionV1(
                ReviewMeshDecisionState
                .BLOCKED_SECURITY_OR_PRIVACY,
                policy.minimum_independent_families,
                empty,
                empty,
                empty,
                "privacy-evidence-not-passing",
            )

    if (
        trusted.fixer_convergence_clear
        is not TrustedCheckStatus.PASS
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .BLOCKED_FIXER_CONVERGENCE,
            policy.minimum_independent_families,
            empty,
            empty,
            empty,
            "fixer-convergence-not-clear",
        )

    if inherited_findings.unresolved:
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .BLOCKED_MATERIAL_FINDING,
            policy.minimum_independent_families,
            empty,
            empty,
            empty,
            "unresolved-material-finding",
        )

    return None


def evaluate_quorum(
    *,
    results: tuple[
        ReviewResultPayloadV1,
        ...
    ],

    active: ActiveReviewBindingsV1,

    contributor_history: ContributorHistoryV1,

    contributor_identities: Mapping[
        str,
        ObservedIdentityFactsV1,
    ],

    lineage_registry: LineageRegistrySnapshotV1,

    qualification_by_evidence_digest: Mapping[
        str,
        QualificationEligibilityV1,
    ],

    policy: QuorumPolicyV1,

    inherited_findings: InheritedFindingSetV1,

    trusted: TrustedOwnerGateInputsV1,
) -> QuorumDecisionV1:
    prerequisite = (
        _terminal_prerequisite_decision(
            policy=policy,
            campaign=active.campaign,
            trusted=trusted,
            inherited_findings=inherited_findings,
        )
    )

    if prerequisite is not None:
        return prerequisite

    if (
        contributor_history
        != active.contributor_history
        or lineage_registry
        != active.lineage_registry
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .BLOCKED_IDENTITY_RECONCILIATION,
            policy.minimum_independent_families,
            (),
            (),
            (),
            "active-provenance-object-mismatch",
        )

    counted_results = []
    counted_lineages = []
    counted_reviewers = []
    rejected = []

    seen_result_digests = set()

    invocation_to_result = {}
    receipt_to_result = {}
    nonce_to_result = {}

    saw_non_independent = False
    saw_unverified_identity = False

    for result in sorted(
        results,
        key=lambda item: (
            item.review_result_digest
        ),
    ):
        digest = (
            result.review_result_digest
        )

        if digest in seen_result_digests:
            # Exact duplicate delivery contributes no additional vote.
            rejected.append(
                VoteRejectionV1(
                    digest,
                    "exact-duplicate-result",
                )
            )
            continue

        seen_result_digests.add(
            digest
        )

        identity = (
            result.reviewer_identity
        )

        duplicate_keys = (
            (
                "invocation",
                identity.invocation_id,
                invocation_to_result,
            ),
            (
                "execution-receipt",
                result.execution_receipt_digest,
                receipt_to_result,
            ),
            (
                "request-nonce",
                result.request.request_nonce,
                nonce_to_result,
            ),
        )

        conflict = None

        for (
            label,
            key,
            registry,
        ) in duplicate_keys:
            previous = registry.get(
                key
            )

            if (
                previous is not None
                and previous != digest
            ):
                conflict = label
                break

        if conflict is not None:
            return QuorumDecisionV1(
                ReviewMeshDecisionState
                .UNTRUSTED_RESULT,
                policy.minimum_independent_families,
                tuple(counted_results),
                tuple(counted_lineages),
                tuple(rejected),
                (
                    "conflicting-reuse-of-"
                    + conflict
                ),
            )

        invocation_to_result[
            identity.invocation_id
        ] = digest

        receipt_to_result[
            result.execution_receipt_digest
        ] = digest

        nonce_to_result[
            result.request.request_nonce
        ] = digest

        freshness = (
            evaluate_result_freshness(
                result,
                active,
            )
        )

        if (
            freshness.status
            is not FreshnessStatus.CURRENT
        ):
            rejected.append(
                VoteRejectionV1(
                    digest,
                    (
                        "result-"
                        + freshness.status.value.lower()
                    ),
                )
            )
            continue

        if (
            result.claimed_verdict
            is not ReviewVerdict.PASS
        ):
            rejected.append(
                VoteRejectionV1(
                    digest,
                    "non-pass-verdict",
                )
            )
            continue

        eligibility = (
            qualification_by_evidence_digest
            .get(
                identity
                .qualification_evidence_digest
            )
        )

        if (
            eligibility is None
            or not eligibility.covers(
                result=result,
                policy=policy,
            )
        ):
            saw_unverified_identity = True

            rejected.append(
                VoteRejectionV1(
                    digest,
                    "qualification-not-current-or-insufficient",
                )
            )

            continue

        reviewer_facts = (
            ObservedIdentityFactsV1
            .from_review_identity(
                identity
            )
        )

        independence = (
            evaluate_independence(
                reviewer=reviewer_facts,

                contributor_history=(
                    contributor_history
                ),

                contributor_identities=(
                    contributor_identities
                ),

                lineage_registry=(
                    lineage_registry
                ),

                other_counted_reviewers=(
                    tuple(
                        counted_reviewers
                    )
                ),
            )
        )

        if (
            independence.status
            is IndependenceStatus
            .BLOCKED_IDENTITY_RECONCILIATION
        ):
            return QuorumDecisionV1(
                ReviewMeshDecisionState
                .BLOCKED_IDENTITY_RECONCILIATION,
                policy.minimum_independent_families,
                tuple(counted_results),
                tuple(counted_lineages),
                tuple(rejected),
                independence.reason,
            )

        if (
            independence.status
            is not IndependenceStatus.INDEPENDENT
        ):
            saw_non_independent = True

            rejected.append(
                VoteRejectionV1(
                    digest,
                    independence.reason,
                )
            )

            continue

        lineage = (
            independence
            .reviewer_lineage_class
        )

        assert lineage is not None

        counted_results.append(
            digest
        )

        counted_lineages.append(
            lineage
        )

        counted_reviewers.append(
            reviewer_facts
        )

    if (
        len(counted_lineages)
        >= policy.minimum_independent_families
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .OWNER_GATE_READY,
            policy.minimum_independent_families,
            tuple(counted_results),
            tuple(counted_lineages),
            tuple(rejected),
            "all-owner-gate-preconditions-satisfied",
        )

    if saw_unverified_identity:
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .UNVERIFIED_IDENTITY,
            policy.minimum_independent_families,
            tuple(counted_results),
            tuple(counted_lineages),
            tuple(rejected),
            "qualified-current-reviewer-evidence-missing",
        )

    if saw_non_independent:
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .NON_INDEPENDENT,
            policy.minimum_independent_families,
            tuple(counted_results),
            tuple(counted_lineages),
            tuple(rejected),
            "insufficient-independent-foundation-families",
        )

    if (
        trusted.reviewer_capacity
        is ReviewerCapacityState
        .TEMPORARILY_UNAVAILABLE
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .WAITING_FOR_INDEPENDENT_REVIEW,
            policy.minimum_independent_families,
            tuple(counted_results),
            tuple(counted_lineages),
            tuple(rejected),
            "only-missing-condition-is-reviewer-capacity",
        )

    if (
        trusted.reviewer_capacity
        is ReviewerCapacityState.UNKNOWN
    ):
        return QuorumDecisionV1(
            ReviewMeshDecisionState
            .BLOCKED_REVIEW_CAPACITY_RECONCILIATION,
            policy.minimum_independent_families,
            tuple(counted_results),
            tuple(counted_lineages),
            tuple(rejected),
            "reviewer-capacity-state-ambiguous",
        )

    return QuorumDecisionV1(
        ReviewMeshDecisionState
        .REVIEW_IN_PROGRESS,
        policy.minimum_independent_families,
        tuple(counted_results),
        tuple(counted_lineages),
        tuple(rejected),
        "additional-qualified-independent-review-required",
    )
