"""Deterministic G0-A Review Mesh orchestration integration.

This module joins the existing Workflow Supervisor contracts to the
Review Mesh protocol without granting any model operational authority.

Pipeline:

validated ReviewTaskSpec
-> exact CandidateIdentity
-> CampaignContextV1
-> ReviewRequestV1
-> ReviewResultPayloadV1
-> ResultIngestionV1
-> append-only Review Mesh ledger
-> deterministic quorum / Owner-gate decision

Important authority boundary:

OWNER_GATE_READY is evidence only. This module exposes no Git merge,
deploy, runtime activation, service restart or provider execution API.

G0-A V1 currently admits COMMIT CandidateIdentity only. The existing
Supervisor also supports TREE_MANIFEST candidates; those fail closed here
until Review Mesh defines an equally exact non-commit candidate reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .supervisor_contracts import (
    CandidateIdentity,
)

from .supervisor_round2_common import (
    ReviewTaskSpec,
)

from .review_mesh_protocol import (
    CampaignContextV1,
    PrivacyClass,
    ResultIngestionV1,
    ReviewRequestV1,
    ReviewResultPayloadV1,
    RiskLevel,
    canonical_digest,
    validate_review_result_stable_mapping_v1,
)

from .review_mesh_decisions import (
    ActiveReviewBindingsV1,
    ContributorHistoryV1,
    InheritedFindingSetV1,
    LineageRegistrySnapshotV1,
    ObservedIdentityFactsV1,
)

from .review_mesh_ledger import (
    LedgerRecordType,
    LedgerRecordV1,
    LedgerReconciliationError,
)

from .review_mesh_ledger_store import (
    ReviewMeshLedgerAuthorityV1,
    ReviewMeshLedgerSnapshotV1,
    ReviewMeshLedgerStoreV1,
    StoredLedgerEntryV1,
)

from .review_mesh_quorum import (
    QualificationEligibilityV1,
    QuorumDecisionV1,
    QuorumPolicyV1,
    ReviewerCapacityState,
    ReviewMeshDecisionState,
    TrustedCheckStatus,
    TrustedOwnerGateInputsV1,
    VoteRejectionV1,
    evaluate_quorum,
)


RESULT_LEDGER_SCHEMA = (
    "REVIEW_RESULT_LEDGER_PAYLOAD_V1"
)

INGESTION_LEDGER_SCHEMA = (
    "RESULT_INGESTION_LEDGER_PAYLOAD_V1"
)


@dataclass(frozen=True)
class CampaignBindingInputsV1:
    repository_id: str
    task_id: str
    source_work_unit_id: str

    review_round: int
    candidate_generation: int
    review_generation: int

    privacy_class: PrivacyClass

    local_gate_evidence_digest: str

    policy_revision: str
    policy_decision_digest: str
    risk_decision_digest: str

    egress_decision_digest: str
    privacy_decision_digest: str

    qualification_registry_snapshot_digest: str

    benchmark_harness_policy_revision: str
    campaign_retry_policy_digest: str


@dataclass(frozen=True)
class BoundReviewCampaignV1:
    campaign: CampaignContextV1

    validated_task_spec_digest: str
    candidate_identity_digest: str

    review_scope_manifest_digest: str
    reviewed_material_digest: str


@dataclass(frozen=True)
class RecordedReviewEvidenceV1:
    snapshot: ReviewMeshLedgerSnapshotV1

    result_record_digest: str
    ingestion_record_digest: str

    result_duplicate: bool
    ingestion_duplicate: bool


@dataclass(frozen=True)
class DurableRecordedReviewEvidenceV1:
    """Result evidence committed through one pinned durable authority."""

    authority: ReviewMeshLedgerAuthorityV1
    snapshot: ReviewMeshLedgerSnapshotV1

    result_record_digest: str
    ingestion_record_digest: str

    result_duplicate: bool
    ingestion_duplicate: bool


@dataclass(frozen=True)
class OrchestratorGateInputsV1:
    """Owner-private trust inputs, never review/model-supplied data.

    Both ledger digests must be loaded from the orchestrator's independently
    persisted trusted checkpoint.  Deriving them from the same authority value
    being evaluated would erase the rollback/fork trust boundary.  As with the
    deterministic/security/privacy statuses below, provenance is established
    by the trusted caller rather than by an unkeyed digest alone.
    """

    ledger_authority_identity_digest: str
    ledger_authority_checkpoint_digest: str

    deterministic_gates: TrustedCheckStatus
    security_evidence: TrustedCheckStatus
    privacy_evidence: TrustedCheckStatus

    fixer_convergence_clear: TrustedCheckStatus

    reviewer_capacity: ReviewerCapacityState

    # Required whenever a G0-B authoritative registry was compiled into the
    # G0-A decision view.  Legacy G0-A-only callers leave this unset.
    bootstrap_complete_payload_digest: str | None = None

    def __post_init__(self):
        for label, value in (
            (
                "ledger authority identity digest",
                self.ledger_authority_identity_digest,
            ),
            (
                "ledger authority checkpoint digest",
                self.ledger_authority_checkpoint_digest,
            ),
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in value
                )
            ):
                raise ValueError(
                    f"{label} is invalid"
                )

        if self.bootstrap_complete_payload_digest is not None:
            value = self.bootstrap_complete_payload_digest
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("bootstrap completion payload digest is invalid")


def _candidate_identity_digest(
    candidate: CandidateIdentity,
) -> str:
    return canonical_digest(
        candidate.stable_payload()
    )


def bind_review_task(
    *,
    spec: ReviewTaskSpec,

    bindings: CampaignBindingInputsV1,

    contributor_history: ContributorHistoryV1,

    lineage_registry: LineageRegistrySnapshotV1,

    quorum_policy: QuorumPolicyV1,
) -> BoundReviewCampaignV1:
    """Validate existing Supervisor state and bind one Mesh campaign."""

    validated = spec.validate()

    objective = (
        spec.task_objective
    )

    candidate = (
        spec.candidate_identity
    )

    if objective is None:
        raise ValueError(
            "Review Mesh requires immutable TaskObjective"
        )

    if candidate is None:
        raise ValueError(
            "Review Mesh requires exact CandidateIdentity"
        )

    objective_mapping = (
        objective.to_mapping()
    )

    if (
        validated.get(
            "task_objective"
        )
        != objective_mapping
    ):
        raise ValueError(
            "validated task objective mismatch"
        )

    objective_sha256 = (
        validated.get(
            "objective_sha256"
        )
    )

    objective_manifest_hash = (
        validated.get(
            "objective_manifest_hash"
        )
    )

    if (
        not objective_sha256
        or objective_sha256
        != spec.objective_sha256
    ):
        raise ValueError(
            "objective digest binding missing"
        )

    if (
        not objective_manifest_hash
        or objective_manifest_hash
        != spec.objective_manifest_hash
    ):
        raise ValueError(
            "objective manifest binding missing"
        )

    source = (
        objective.source_work_unit_id
        or bindings.source_work_unit_id
    )

    if (
        objective.source_work_unit_id
        is not None
        and objective.source_work_unit_id
        != bindings.source_work_unit_id
    ):
        raise ValueError(
            "source work unit binding mismatch"
        )

    if (
        candidate.candidate_ref_type
        != "COMMIT"
        or not candidate.candidate_commit_sha
    ):
        raise ValueError(
            "G0-A Review Mesh requires committed candidate"
        )

    candidate_digest = (
        _candidate_identity_digest(
            candidate
        )
    )

    if (
        contributor_history
        .latest_candidate_identity_digest
        != candidate_digest
    ):
        raise ValueError(
            "contributor history is not bound to candidate"
        )

    if (
        contributor_history.entries[-1]
        .candidate_generation
        != bindings.candidate_generation
    ):
        raise ValueError(
            "candidate generation does not match contributor history"
        )

    if (
        lineage_registry.binding_digest
        == ""
    ):
        raise ValueError(
            "lineage registry snapshot missing"
        )

    try:
        risk_level = RiskLevel(
            str(
                validated[
                    "risk_level"
                ]
            )
        )
    except (
        KeyError,
        ValueError,
    ) as error:
        raise ValueError(
            "validated review risk level is unsupported"
        ) from error

    if (
        quorum_policy.risk_level
        is not risk_level
    ):
        raise ValueError(
            "quorum policy risk does not match ReviewTaskSpec"
        )

    safe_manifest = tuple(
        validated.get(
            "safe_file_manifest",
            (),
        )
    )

    if not safe_manifest:
        raise ValueError(
            "Review Mesh requires non-empty safe file manifest"
        )

    allowed_paths = tuple(
        sorted(
            str(value)
            for value in validated.get(
                "allowed_paths",
                ()
            )
        )
    )

    if not allowed_paths:
        raise ValueError(
            "Review Mesh requires bounded review scope"
        )

    scope_digest = canonical_digest({
        "allowed_paths":
            list(
                allowed_paths
            ),

        "safe_file_paths":
            sorted(
                str(
                    item["path"]
                )
                for item in safe_manifest
            ),
    })

    material_digest = canonical_digest({
        "task_prompt_sha256":
            validated[
                "task_prompt_sha256"
            ],

        "safe_file_manifest":
            list(
                safe_manifest
            ),

        "candidate_identity_digest":
            candidate_digest,

        "candidate_diff_sha256":
            candidate
            .candidate_diff_sha256,
    })

    campaign = CampaignContextV1(
        repository_id=(
            bindings.repository_id
        ),

        task_id=(
            bindings.task_id
        ),

        source_work_unit_id=(
            source
        ),

        review_round=(
            bindings.review_round
        ),

        candidate_generation=(
            bindings.candidate_generation
        ),

        review_generation=(
            bindings.review_generation
        ),

        objective_sha256=(
            str(
                objective_sha256
            )
        ),

        objective_manifest_hash=(
            str(
                objective_manifest_hash
            )
        ),

        candidate_identity_digest=(
            candidate_digest
        ),

        base_sha=(
            candidate
            .base_commit_sha
        ),

        candidate_sha=(
            candidate
            .candidate_commit_sha
        ),

        candidate_diff_sha256=(
            candidate
            .candidate_diff_sha256
        ),

        review_scope_manifest_digest=(
            scope_digest
        ),

        reviewed_material_digest=(
            material_digest
        ),

        contributor_set_digest=(
            contributor_history
            .contributor_set_digest
        ),

        local_gate_evidence_digest=(
            bindings
            .local_gate_evidence_digest
        ),

        policy_revision=(
            bindings
            .policy_revision
        ),

        policy_decision_digest=(
            bindings
            .policy_decision_digest
        ),

        risk_level=(
            risk_level
        ),

        risk_decision_digest=(
            bindings
            .risk_decision_digest
        ),

        privacy_class=(
            bindings
            .privacy_class
        ),

        egress_decision_digest=(
            bindings
            .egress_decision_digest
        ),

        privacy_decision_digest=(
            bindings
            .privacy_decision_digest
        ),

        required_reviewer_class=(
            quorum_policy
            .required_reviewer_class
        ),

        quorum_policy_digest=(
            quorum_policy
            .policy_digest
        ),

        lineage_registry_snapshot_digest=(
            lineage_registry
            .binding_digest
        ),

        qualification_registry_snapshot_digest=(
            bindings
            .qualification_registry_snapshot_digest
        ),

        benchmark_harness_policy_revision=(
            bindings
            .benchmark_harness_policy_revision
        ),

        campaign_retry_policy_digest=(
            bindings
            .campaign_retry_policy_digest
        ),
    )

    return BoundReviewCampaignV1(
        campaign=campaign,

        validated_task_spec_digest=(
            canonical_digest(
                validated
            )
        ),

        candidate_identity_digest=(
            candidate_digest
        ),

        review_scope_manifest_digest=(
            scope_digest
        ),

        reviewed_material_digest=(
            material_digest
        ),
    )


def build_review_request(
    bound: BoundReviewCampaignV1,
    *,
    review_work_unit_id: str,
    reviewer_lane: str,
    required_adapter_principal: str,

    lane_attempt: int,
    request_nonce: str,

    created_at: str,
    request_expiry_at: str,
) -> ReviewRequestV1:
    return ReviewRequestV1(
        campaign=(
            bound.campaign
        ),

        review_work_unit_id=(
            review_work_unit_id
        ),

        reviewer_lane=(
            reviewer_lane
        ),

        required_adapter_principal=(
            required_adapter_principal
        ),

        lane_attempt=(
            lane_attempt
        ),

        request_nonce=(
            request_nonce
        ),

        created_at=(
            created_at
        ),

        request_expiry_at=(
            request_expiry_at
        ),
    )


def review_result_ledger_payload(
    result: ReviewResultPayloadV1,
) -> dict:
    return {
        "schema_version":
            RESULT_LEDGER_SCHEMA,

        "review_result_id":
            result.review_result_id,

        "review_result_digest":
            result.review_result_digest,

        "review_result_payload":
            result.stable_mapping(),
    }


def result_ingestion_ledger_payload(
    ingestion: ResultIngestionV1,
) -> dict:
    return {
        "schema_version":
            INGESTION_LEDGER_SCHEMA,

        "ingestion_payload_digest":
            ingestion
            .ingestion_payload_digest,

        "ingestion_payload":
            ingestion
            .stable_mapping(),
    }


def _append_snapshot(
    snapshot: ReviewMeshLedgerSnapshotV1,
    *,
    record_type: LedgerRecordType,
    payload: Mapping,

    related_task_id: str,
    related_request_id: str | None,
    related_campaign_id: str | None,

    actor_provenance_digest: str,
    ingestion_receipt_digest: str,

    idempotency_key: str,
    created_at: str,

    superseded_or_revoked_record_digest: (
        str | None
    ) = None,
) -> tuple[
    ReviewMeshLedgerSnapshotV1,
    LedgerRecordV1,
    bool,
]:
    outcome = snapshot.ledger.append(
        record_type=record_type,
        payload=payload,

        related_task_id=(
            related_task_id
        ),

        related_request_id=(
            related_request_id
        ),

        related_campaign_id=(
            related_campaign_id
        ),

        actor_provenance_digest=(
            actor_provenance_digest
        ),

        ingestion_receipt_digest=(
            ingestion_receipt_digest
        ),

        idempotency_key=(
            idempotency_key
        ),

        created_at=(
            created_at
        ),

        superseded_or_revoked_record_digest=(
            superseded_or_revoked_record_digest
        ),
    )

    if outcome.duplicate:
        return (
            snapshot,
            outcome.record,
            True,
        )

    entry = (
        StoredLedgerEntryV1
        .from_payload(
            outcome.record,
            payload,
        )
    )

    updated = (
        ReviewMeshLedgerSnapshotV1(
            snapshot.entries
            + (
                entry,
            )
        )
    )

    return (
        updated,
        outcome.record,
        False,
    )


def record_result_evidence(
    *,
    snapshot: ReviewMeshLedgerSnapshotV1,

    bound: BoundReviewCampaignV1,

    result: ReviewResultPayloadV1,

    ingestion: ResultIngestionV1,
) -> RecordedReviewEvidenceV1:
    if (
        result.request.campaign
        .campaign_context_digest
        != bound.campaign
        .campaign_context_digest
    ):
        raise ValueError(
            "result does not belong to bound campaign"
        )

    if (
        ingestion.result
        .review_result_digest
        != result.review_result_digest
    ):
        raise ValueError(
            "ingestion does not bind exact result"
        )

    result_payload = (
        review_result_ledger_payload(
            result
        )
    )

    (
        after_result,
        result_record,
        result_duplicate,
    ) = _append_snapshot(
        snapshot,

        record_type=(
            LedgerRecordType
            .REVIEW_RESULT
        ),

        payload=(
            result_payload
        ),

        related_task_id=(
            bound.campaign.task_id
        ),

        related_request_id=(
            result.request
            .review_request_id
        ),

        related_campaign_id=(
            bound.campaign
            .review_campaign_id
        ),

        actor_provenance_digest=(
            result.reviewer_identity
            .identity_envelope_digest
        ),

        ingestion_receipt_digest=(
            result.reviewer_identity
            .authenticated_ingestion_receipt_digest
        ),

        idempotency_key=(
            "review-result:"
            + result.review_result_digest
        ),

        created_at=(
            result
            .invocation_completed_at
        ),
    )

    ingestion_payload = (
        result_ingestion_ledger_payload(
            ingestion
        )
    )

    (
        after_ingestion,
        ingestion_record,
        ingestion_duplicate,
    ) = _append_snapshot(
        after_result,

        record_type=(
            LedgerRecordType
            .RESULT_INGESTION
        ),

        payload=(
            ingestion_payload
        ),

        related_task_id=(
            bound.campaign.task_id
        ),

        related_request_id=(
            result.request
            .review_request_id
        ),

        related_campaign_id=(
            bound.campaign
            .review_campaign_id
        ),

        actor_provenance_digest=(
            result.reviewer_identity
            .identity_envelope_digest
        ),

        ingestion_receipt_digest=(
            ingestion
            .authenticated_ingestion_receipt_digest
        ),

        idempotency_key=(
            "result-ingestion:"
            + ingestion
            .ingestion_payload_digest
        ),

        created_at=(
            ingestion
            .orchestrator_ingested_at
        ),
    )

    return RecordedReviewEvidenceV1(
        snapshot=(
            after_ingestion
        ),

        result_record_digest=(
            result_record
            .record_digest
        ),

        ingestion_record_digest=(
            ingestion_record
            .record_digest
        ),

        result_duplicate=(
            result_duplicate
        ),

        ingestion_duplicate=(
            ingestion_duplicate
        ),
    )


def record_result_evidence_durably(
    *,
    ledger_store: ReviewMeshLedgerStoreV1,
    ledger_authority: ReviewMeshLedgerAuthorityV1,

    bound: BoundReviewCampaignV1,
    result: ReviewResultPayloadV1,
    ingestion: ResultIngestionV1,
) -> DurableRecordedReviewEvidenceV1:
    """Commit result evidence through the pinned durable authority.

    Each append compares the authority's trusted current head before
    advancing its immutable head/count checkpoint. An in-memory snapshot
    can still be useful for pure protocol tests, but it is never sufficient
    for Owner-gate evaluation.

    The trusted orchestrator must durably persist the returned authority.
    If execution stops after a ledger write but before that checkpoint is
    persisted, callers must enter explicit reconciliation rather than derive
    or adopt a replacement checkpoint from the ledger's current self-report.
    """

    if (
        result.request.campaign
        .campaign_context_digest
        != bound.campaign
        .campaign_context_digest
    ):
        raise ValueError(
            "result does not belong to bound campaign"
        )

    if (
        ingestion.result
        .review_result_digest
        != result.review_result_digest
    ):
        raise ValueError(
            "ingestion does not bind exact result"
        )

    result_outcome = (
        ledger_store
        .append_authoritatively(
            authority=ledger_authority,

            record_type=(
                LedgerRecordType
                .REVIEW_RESULT
            ),

            payload=(
                review_result_ledger_payload(
                    result
                )
            ),

            related_task_id=(
                bound.campaign.task_id
            ),

            related_request_id=(
                result.request
                .review_request_id
            ),

            related_campaign_id=(
                bound.campaign
                .review_campaign_id
            ),

            actor_provenance_digest=(
                result.reviewer_identity
                .identity_envelope_digest
            ),

            ingestion_receipt_digest=(
                result.reviewer_identity
                .authenticated_ingestion_receipt_digest
            ),

            idempotency_key=(
                "review-result:"
                + result.review_result_digest
            ),

            created_at=(
                result
                .invocation_completed_at
            ),
        )
    )

    ingestion_outcome = (
        ledger_store
        .append_authoritatively(
            authority=(
                result_outcome.authority
            ),

            record_type=(
                LedgerRecordType
                .RESULT_INGESTION
            ),

            payload=(
                result_ingestion_ledger_payload(
                    ingestion
                )
            ),

            related_task_id=(
                bound.campaign.task_id
            ),

            related_request_id=(
                result.request
                .review_request_id
            ),

            related_campaign_id=(
                bound.campaign
                .review_campaign_id
            ),

            actor_provenance_digest=(
                result.reviewer_identity
                .identity_envelope_digest
            ),

            ingestion_receipt_digest=(
                ingestion
                .authenticated_ingestion_receipt_digest
            ),

            idempotency_key=(
                "result-ingestion:"
                + ingestion
                .ingestion_payload_digest
            ),

            created_at=(
                ingestion
                .orchestrator_ingested_at
            ),
        )
    )

    return DurableRecordedReviewEvidenceV1(
        authority=(
            ingestion_outcome.authority
        ),

        snapshot=(
            ingestion_outcome.snapshot
        ),

        result_record_digest=(
            result_outcome.record
            .record_digest
        ),

        ingestion_record_digest=(
            ingestion_outcome.record
            .record_digest
        ),

        result_duplicate=(
            result_outcome.duplicate
        ),

        ingestion_duplicate=(
            ingestion_outcome.duplicate
        ),
    )


def _tombstoned_record_digests(
    snapshot: ReviewMeshLedgerSnapshotV1,
) -> frozenset[str]:
    return frozenset(
        entry.record
        .superseded_or_revoked_record_digest

        for entry in snapshot.entries

        if (
            entry.record.record_type
            is LedgerRecordType.TOMBSTONE

            and entry.record
            .superseded_or_revoked_record_digest
            is not None
        )
    )


def result_has_active_ledger_evidence(
    *,
    snapshot: ReviewMeshLedgerSnapshotV1,

    bound: BoundReviewCampaignV1,

    result: ReviewResultPayloadV1,
) -> bool:
    """Require one active result record plus one active ingestion record."""

    tombstoned = (
        _tombstoned_record_digests(
            snapshot
        )
    )

    expected_result_payload = (
        review_result_ledger_payload(
            result
        )
    )

    result_matches = [
        entry

        for entry in snapshot.entries

        if (
            entry.record.record_type
            is LedgerRecordType
            .REVIEW_RESULT

            and entry.record.record_digest
            not in tombstoned

            and entry.record.related_task_id
            == bound.campaign.task_id

            and entry.record.related_request_id
            == result.request.review_request_id

            and entry.record.related_campaign_id
            == bound.campaign.review_campaign_id

            and entry.payload_mapping()
            == expected_result_payload
        )
    ]

    if len(result_matches) != 1:
        return False

    ingestion_matches = []

    for entry in snapshot.entries:
        if (
            entry.record.record_type
            is not LedgerRecordType
            .RESULT_INGESTION
            or entry.record.record_digest
            in tombstoned
            or entry.record.related_task_id
            != bound.campaign.task_id
            or entry.record.related_request_id
            != result.request.review_request_id
            or entry.record.related_campaign_id
            != bound.campaign.review_campaign_id
        ):
            continue

        outer = (
            entry.payload_mapping()
        )

        if set(
            outer
        ) != {
            "schema_version",
            "ingestion_payload_digest",
            "ingestion_payload",
        }:
            continue

        if (
            outer[
                "schema_version"
            ]
            != INGESTION_LEDGER_SCHEMA
        ):
            continue

        inner = (
            outer[
                "ingestion_payload"
            ]
        )

        if not isinstance(
            inner,
            dict,
        ):
            continue

        if (
            outer[
                "ingestion_payload_digest"
            ]
            != canonical_digest(
                inner
            )
        ):
            continue

        required_bindings = {
            "review_result_id":
                result
                .review_result_id,

            "review_result_digest":
                result
                .review_result_digest,

            "review_request_id":
                result.request
                .review_request_id,

            "request_digest":
                result.request
                .request_digest,

            "reviewer_identity_envelope_digest":
                result.reviewer_identity
                .identity_envelope_digest,

            "invocation_id":
                result
                .invocation_id,

            "execution_receipt_digest":
                result
                .execution_receipt_digest,
        }

        if any(
            inner.get(key)
            != value

            for key, value
            in required_bindings.items()
        ):
            continue

        ingestion_matches.append(
            entry
        )

    return (
        len(
            ingestion_matches
        )
        == 1
    )


def _cross_campaign_vote_reuse_rejections(
    *,
    snapshot: ReviewMeshLedgerSnapshotV1,

    bound: BoundReviewCampaignV1,

    results: tuple[
        ReviewResultPayloadV1,
        ...
    ],
) -> tuple[
    VoteRejectionV1,
    ...
]:
    """Reject reuse of one accepted execution across campaigns.

    REVIEW_MESH_PROTOCOL_V1 sections 10 and 15 require one
    invocation/execution nonce, execution receipt and result digest
    to contribute at most one vote, including across campaigns.

    This evidence is derived from the append-only ledger. It is not
    accepted as a caller-supplied trust assertion.
    """

    current_campaign_id = (
        bound.campaign
        .review_campaign_id
    )

    historical = {
        "review-result-digest":
            set(),

        "invocation-id":
            set(),

        "execution-nonce":
            set(),

        "execution-receipt":
            set(),
    }

    for entry in snapshot.entries:
        record = entry.record

        if (
            record.record_type
            is not LedgerRecordType
            .REVIEW_RESULT
        ):
            continue

        outer = (
            entry.payload_mapping()
        )

        if set(outer) != {
            "schema_version",
            "review_result_id",
            "review_result_digest",
            "review_result_payload",
        }:
            raise ValueError(
                "historical review-result ledger schema mismatch"
            )

        if any(
            type(outer[field]) is not str
            for field in (
                "schema_version",
                "review_result_id",
                "review_result_digest",
            )
        ):
            raise ValueError(
                "historical review-result ledger field type mismatch"
            )

        if (
            outer["schema_version"]
            != RESULT_LEDGER_SCHEMA
        ):
            raise ValueError(
                "historical review-result schema version mismatch"
            )

        inner = validate_review_result_stable_mapping_v1(
            outer[
                "review_result_payload"
            ]
        )

        historical_digest = (
            canonical_digest(
                inner
            )
        )

        if (
            outer[
                "review_result_digest"
            ]
            != historical_digest
        ):
            raise ValueError(
                "historical review-result digest mismatch"
            )

        if (
            outer[
                "review_result_id"
            ]
            != (
                "rrs1:"
                + historical_digest
            )
        ):
            raise ValueError(
                "historical review-result id mismatch"
            )

        if (
            record.related_request_id
            != inner["review_request_id"]
            or record.related_campaign_id
            != inner["review_campaign_id"]
            or record.actor_provenance_digest
            != inner[
                "reviewer_identity_envelope_digest"
            ]
            or record.created_at
            != inner[
                "invocation_completed_at"
            ]
        ):
            raise ValueError(
                "historical review-result ledger header binding mismatch"
            )

        if (
            record.related_campaign_id
            == current_campaign_id
        ):
            continue

        historical[
            "review-result-digest"
        ].add(
            historical_digest
        )

        historical[
            "invocation-id"
        ].add(
            inner[
                "invocation_id"
            ]
        )

        historical[
            "execution-nonce"
        ].add(
            inner[
                "execution_nonce"
            ]
        )

        historical[
            "execution-receipt"
        ].add(
            inner[
                "execution_receipt_digest"
            ]
        )

    rejected = []

    for result in results:
        checks = (
            (
                "review-result-digest",
                result.review_result_digest,
            ),
            (
                "invocation-id",
                result.invocation_id,
            ),
            (
                "execution-nonce",
                result.execution_nonce,
            ),
            (
                "execution-receipt",
                result.execution_receipt_digest,
            ),
        )

        for label, value in checks:
            if value not in historical[
                label
            ]:
                continue

            rejected.append(
                VoteRejectionV1(
                    result_digest=(
                        result
                        .review_result_digest
                    ),

                    reason=(
                        "cross-campaign-"
                        + label
                        + "-reuse"
                    ),
                )
            )

            # One deterministic reason per result is sufficient.
            break

    return tuple(
        rejected
    )


def _derive_contributor_provenance(
    *,
    bound: BoundReviewCampaignV1,

    contributor_history: ContributorHistoryV1,

    contributor_identities: Mapping[
        str,
        ObservedIdentityFactsV1,
    ],

    lineage_registry: LineageRegistrySnapshotV1,
) -> TrustedCheckStatus:
    if (
        contributor_history
        .contributor_set_digest
        != bound.campaign
        .contributor_set_digest
    ):
        return (
            TrustedCheckStatus.FAIL
        )

    if (
        contributor_history
        .latest_candidate_identity_digest
        != bound.candidate_identity_digest
    ):
        return (
            TrustedCheckStatus.FAIL
        )

    if (
        lineage_registry
        .binding_digest
        != bound.campaign
        .lineage_registry_snapshot_digest
    ):
        return (
            TrustedCheckStatus.FAIL
        )

    for entry in (
        contributor_history.entries
    ):
        facts = (
            contributor_identities
            .get(
                entry
                .identity_envelope_digest
            )
        )

        if facts is None:
            return (
                TrustedCheckStatus.UNKNOWN
            )

        if (
            facts.identity_envelope_digest
            != entry
            .identity_envelope_digest
        ):
            return (
                TrustedCheckStatus.FAIL
            )

        if (
            lineage_registry.resolve(
                facts
            )
            is None
        ):
            return (
                TrustedCheckStatus.UNKNOWN
            )

    return (
        TrustedCheckStatus.PASS
    )


def evaluate_owner_gate(
    *,
    bound: BoundReviewCampaignV1,

    ledger_store: ReviewMeshLedgerStoreV1,
    ledger_authority: ReviewMeshLedgerAuthorityV1,

    results: tuple[
        ReviewResultPayloadV1,
        ...
    ],

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

    quorum_policy: QuorumPolicyV1,

    inherited_findings: InheritedFindingSetV1,

    gate_inputs: OrchestratorGateInputsV1,
) -> QuorumDecisionV1:
    """Derive trusted durable-ledger state before quorum evaluation.

    A caller-created ``ReviewMeshLedgerSnapshotV1`` is deliberately not an
    input. The configured authority binds the canonical store identity,
    pinned genesis, trusted current head and record-count checkpoint. The
    store reloads and verifies that exact checkpoint on every evaluation.
    """

    snapshot = None
    reuse_rejections = ()
    ledger_ok = False

    try:
        if (
            gate_inputs
            .ledger_authority_identity_digest
            != ledger_authority
            .store_identity_digest
        ):
            raise ValueError(
                "ledger authority identity is not the trusted gate authority"
            )

        if (
            gate_inputs
            .ledger_authority_checkpoint_digest
            != ledger_authority
            .checkpoint_digest
        ):
            raise ValueError(
                "ledger authority checkpoint is not the trusted gate checkpoint"
            )

        snapshot = (
            ledger_store
            .load_authoritative(
                ledger_authority
            )
        )

        ledger_ok = (
            snapshot.ledger
            .verify_continuity()
        )

        if not ledger_ok:
            raise LedgerReconciliationError(
                "authoritative ledger continuity verification failed"
            )

        # A rich G0-B registry view is usable only after the exact bootstrap
        # package is the immutable first ledger record.  The completion payload
        # must bind both authoritative registry snapshots used by this campaign.
        if lineage_registry.source_registry_snapshot_digest is not None:
            from .review_mesh_bootstrap import BootstrapCompletePayloadV1

            expected_bootstrap = gate_inputs.bootstrap_complete_payload_digest
            if expected_bootstrap is None:
                raise LedgerReconciliationError(
                    "G0-B campaign lacks trusted bootstrap completion binding"
                )
            record = snapshot.ledger.require_bootstrap_complete(
                expected_payload_digest=expected_bootstrap,
            )
            stored_matches = tuple(
                entry for entry in snapshot.entries
                if entry.record.record_digest == record.record_digest
            )
            if len(stored_matches) != 1:
                raise LedgerReconciliationError(
                    "bootstrap completion payload is not uniquely durable"
                )
            complete = BootstrapCompletePayloadV1.from_mapping(
                stored_matches[0].payload_mapping()
            )
            if (
                complete.lineage_registry_snapshot_digest
                != lineage_registry.binding_digest
                or complete.qualification_registry_snapshot_digest
                != bound.campaign.qualification_registry_snapshot_digest
            ):
                raise LedgerReconciliationError(
                    "bootstrap completion registry binding mismatch"
                )

        # Reconcile every historical result before trusting current-result
        # evidence. This both reserves execution identities across campaigns
        # and makes malformed durable history a deterministic ledger block.
        reuse_rejections = (
            _cross_campaign_vote_reuse_rejections(
                snapshot=snapshot,
                bound=bound,
                results=results,
            )
        )

        if not reuse_rejections:
            ledger_ok = (
                ledger_ok
                and all(
                    result_has_active_ledger_evidence(
                        snapshot=snapshot,
                        bound=bound,
                        result=result,
                    )
                    for result in results
                )
            )

    except (
        LedgerReconciliationError,
        KeyError,
        OSError,
        ValueError,
    ):
        # Authority, continuity, payload decoding and evidence-binding
        # ambiguity all fail closed to the protocol reconciliation state.
        ledger_ok = False
        reuse_rejections = ()

    if (
        reuse_rejections
    ):
        return QuorumDecisionV1(
            state=(
                ReviewMeshDecisionState
                .UNTRUSTED_RESULT
            ),

            required_independent_families=(
                quorum_policy
                .minimum_independent_families
            ),

            counted_result_digests=(),

            counted_lineage_classes=(),

            rejected_votes=(
                reuse_rejections
            ),

            reason=(
                "cross-campaign-vote-identity-reuse"
            ),
        )

    ledger_status = (
        TrustedCheckStatus.PASS
        if ledger_ok
        else TrustedCheckStatus.FAIL
    )

    provenance_status = (
        _derive_contributor_provenance(
            bound=bound,

            contributor_history=(
                contributor_history
            ),

            contributor_identities=(
                contributor_identities
            ),

            lineage_registry=(
                lineage_registry
            ),
        )
    )

    trusted = (
        TrustedOwnerGateInputsV1(
            ledger_continuity=(
                ledger_status
            ),

            contributor_provenance=(
                provenance_status
            ),

            deterministic_gates=(
                gate_inputs
                .deterministic_gates
            ),

            security_evidence=(
                gate_inputs
                .security_evidence
            ),

            privacy_evidence=(
                gate_inputs
                .privacy_evidence
            ),

            fixer_convergence_clear=(
                gate_inputs
                .fixer_convergence_clear
            ),

            reviewer_capacity=(
                gate_inputs
                .reviewer_capacity
            ),
        )
    )

    active = (
        ActiveReviewBindingsV1(
            campaign=(
                bound.campaign
            ),

            contributor_history=(
                contributor_history
            ),

            lineage_registry=(
                lineage_registry
            ),

            qualification_registry_snapshot_digest=(
                bound.campaign
                .qualification_registry_snapshot_digest
            ),
        )
    )

    return evaluate_quorum(
        results=results,

        active=active,

        contributor_history=(
            contributor_history
        ),

        contributor_identities=(
            contributor_identities
        ),

        lineage_registry=(
            lineage_registry
        ),

        qualification_by_evidence_digest=(
            qualification_by_evidence_digest
        ),

        policy=(
            quorum_policy
        ),

        inherited_findings=(
            inherited_findings
        ),

        trusted=(
            trusted
        ),
    )
