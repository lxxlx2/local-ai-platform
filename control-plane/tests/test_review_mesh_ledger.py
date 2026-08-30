from dataclasses import replace

import pytest

from local_ai_control.services.review_mesh_protocol import (
    canonical_digest,
)

from local_ai_control.services.review_mesh_decisions import (
    FindingState,
    InheritedFindingSetV1,
    MaterialFindingV1,
)

from local_ai_control.services.review_mesh_ledger import (
    FindingLifecycleStateV1,
    FindingProofKind,
    FindingTransitionEventV1,
    IndependentVerificationSetV1,
    LEDGER_GENESIS_DIGEST,
    LedgerRecordType,
    LedgerReconciliationError,
    ReviewMeshLedgerV1,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
E = "e" * 64
F = "f" * 64
G = "0" * 64
H = "1" * 64


def append(
    ledger,
    *,
    key,
    payload=None,
    record_type=LedgerRecordType.REVIEW_RESULT,
    created="2026-08-30T05:00:00+00:00",
    tombstone_target=None,
):
    return ledger.append(
        record_type=record_type,

        payload=(
            {"value": key}
            if payload is None
            else payload
        ),

        related_task_id="g0a-slice4",

        related_request_id=(
            "rr1:" + A
        ),

        related_campaign_id=(
            "rc1:" + B
        ),

        actor_provenance_digest=C,
        ingestion_receipt_digest=D,

        idempotency_key=key,
        created_at=created,

        superseded_or_revoked_record_digest=(
            tombstone_target
        ),
    )


def material_finding():
    result_digest = A

    content_digest = canonical_digest({
        "scope": "WORKFLOW",
        "severity": "HIGH",
        "file": None,
        "evidence": "stale review can replay",
        "recommended_fix": "bind generation",
    })

    finding_id = (
        MaterialFindingV1
        .compute_finding_id(
            originating_result_digest=(
                result_digest
            ),
            finding_ordinal=1,
            finding_content_digest=(
                content_digest
            ),
        )
    )

    return MaterialFindingV1(
        finding_id=finding_id,

        originating_result_digest=(
            result_digest
        ),

        finding_ordinal=1,

        finding_content_digest=(
            content_digest
        ),

        candidate_generation_introduced=1,

        scope="WORKFLOW",
        severity="HIGH",
        file=None,

        evidence="stale review can replay",

        recommended_fix="bind generation",

        state=FindingState.OPEN,
    )


def lifecycle():
    inherited = InheritedFindingSetV1(
        (
            material_finding(),
        )
    )

    return (
        FindingLifecycleStateV1
        .from_inherited(
            inherited,

            current_candidate_generation=1,

            current_candidate_identity_digest=E,
        )
    )


def verification_set(
    *,
    generation=2,
    candidate_digest=F,
    verifiers=(G,),
):
    return IndependentVerificationSetV1(
        candidate_generation=generation,

        candidate_identity_digest=(
            candidate_digest
        ),

        verifier_identity_envelope_digests=(
            verifiers
        ),

        verification_evidence_digest=A,

        independence_decision_digest=B,

        qualification_decision_digest=C,

        policy_decision_digest=D,
    )


def repair_event(
    state,
    *,
    generation=2,
    candidate_digest=F,
    fixer=H,
):
    finding = state.entries[0].finding

    return FindingTransitionEventV1(
        finding_id=finding.finding_id,

        from_state=FindingState.OPEN,
        to_state=FindingState.REPAIR_PROPOSED,

        candidate_generation=generation,

        candidate_identity_digest=(
            candidate_digest
        ),

        actor_identity_envelope_digest=C,

        proof_kind=(
            FindingProofKind.REPAIR_EVIDENCE
        ),

        evidence_digest=D,

        fixer_identity_envelope_digest=(
            fixer
        ),

        created_at=(
            "2026-08-30T05:10:00+00:00"
        ),
    )


def close_event(
    state,
    *,
    fixer=H,
    verifiers=(G,),
):
    entry = state.entries[0]

    assert (
        entry.repair_candidate_generation
        is not None
    )

    assert (
        entry.repair_candidate_identity_digest
        is not None
    )

    return FindingTransitionEventV1(
        finding_id=entry.finding.finding_id,

        from_state=(
            FindingState.REPAIR_PROPOSED
        ),

        to_state=(
            FindingState.VERIFIED_CLOSED
        ),

        candidate_generation=(
            entry.repair_candidate_generation
        ),

        candidate_identity_digest=(
            entry.repair_candidate_identity_digest
        ),

        actor_identity_envelope_digest=C,

        proof_kind=(
            FindingProofKind
            .INDEPENDENT_VERIFICATION
        ),

        evidence_digest=A,

        verification_set=(
            verification_set(
                generation=(
                    entry
                    .repair_candidate_generation
                ),

                candidate_digest=(
                    entry
                    .repair_candidate_identity_digest
                ),

                verifiers=verifiers,
            )
        ),

        created_at=(
            "2026-08-30T05:20:00+00:00"
        ),
    )


def test_empty_ledger_head_is_pinned_genesis():
    ledger = ReviewMeshLedgerV1()

    assert (
        ledger.head_digest
        == LEDGER_GENESIS_DIGEST
    )

    assert ledger.next_sequence == 1
    assert ledger.verify_continuity()


def test_first_record_binds_genesis():
    outcome = append(
        ReviewMeshLedgerV1(),
        key="record-1",
    )

    assert outcome.duplicate is False

    assert (
        outcome.record.sequence_number
        == 1
    )

    assert (
        outcome.record
        .previous_ledger_head_digest
        == LEDGER_GENESIS_DIGEST
    )

    assert (
        outcome.ledger.head_digest
        == outcome.record.record_digest
    )


def test_second_record_binds_previous_record_digest():
    first = append(
        ReviewMeshLedgerV1(),
        key="record-1",
    )

    second = append(
        first.ledger,
        key="record-2",
    )

    assert (
        second.record.sequence_number
        == 2
    )

    assert (
        second.record
        .previous_ledger_head_digest
        == first.record.record_digest
    )


def test_record_digest_has_no_self_reference():
    outcome = append(
        ReviewMeshLedgerV1(),
        key="record-1",
    )

    mapping = (
        outcome.record.digest_mapping()
    )

    assert "record_digest" not in mapping

    assert (
        outcome.record.record_digest
        == canonical_digest(mapping)
    )


def test_exact_duplicate_delivery_is_idempotent():
    first = append(
        ReviewMeshLedgerV1(),
        key="same-delivery",
        payload={"result": A},
    )

    retry = append(
        first.ledger,
        key="same-delivery",
        payload={"result": A},
        created=(
            "2026-08-30T05:05:00+00:00"
        ),
    )

    assert retry.duplicate is True

    assert (
        retry.ledger
        == first.ledger
    )

    assert (
        retry.record
        == first.record
    )


def test_conflicting_idempotency_key_blocks():
    first = append(
        ReviewMeshLedgerV1(),
        key="same-key",
        payload={"result": A},
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="conflicting ledger idempotency key",
    ):
        append(
            first.ledger,
            key="same-key",
            payload={"result": B},
        )


def test_tombstone_must_reference_prior_record():
    with pytest.raises(
        LedgerReconciliationError,
        match="tombstone target",
    ):
        append(
            ReviewMeshLedgerV1(),
            key="tombstone-1",

            record_type=(
                LedgerRecordType.TOMBSTONE
            ),

            tombstone_target=A,
        )


def test_valid_tombstone_is_append_only():
    first = append(
        ReviewMeshLedgerV1(),
        key="record-1",
    )

    tombstone = append(
        first.ledger,
        key="tombstone-1",

        record_type=(
            LedgerRecordType.TOMBSTONE
        ),

        tombstone_target=(
            first.record.record_digest
        ),
    )

    assert len(
        tombstone.ledger.records
    ) == 2

    assert (
        tombstone.record
        .superseded_or_revoked_record_digest
        == first.record.record_digest
    )

    assert (
        tombstone.ledger.records[0]
        == first.record
    )


def test_reordered_chain_is_rejected():
    first = append(
        ReviewMeshLedgerV1(),
        key="record-1",
    )

    second = append(
        first.ledger,
        key="record-2",
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="sequence gap or reorder",
    ):
        ReviewMeshLedgerV1(
            records=(
                second.record,
                first.record,
            )
        )


def test_unpinned_genesis_is_rejected():
    with pytest.raises(
        LedgerReconciliationError,
        match="unpinned ledger genesis",
    ):
        ReviewMeshLedgerV1(
            genesis_digest=A
        )


def test_verification_set_order_is_canonical():
    first = verification_set(
        verifiers=(A, B),
    )

    second = verification_set(
        verifiers=(B, A),
    )

    assert (
        first.verification_set_digest
        == second.verification_set_digest
    )


def test_duplicate_verifier_is_rejected():
    with pytest.raises(
        ValueError,
        match="duplicate verifier",
    ):
        verification_set(
            verifiers=(A, A),
        )


def test_repair_requires_new_candidate_generation():
    state = lifecycle()

    event = repair_event(
        state,
        generation=1,
    )

    with pytest.raises(
        ValueError,
        match="new candidate generation",
    ):
        state.apply(event)


def test_repair_proposal_records_exact_fixer_and_candidate():
    state = lifecycle()

    event = repair_event(
        state
    )

    repaired = state.apply(
        event
    )

    entry = repaired.entries[0]

    assert (
        entry.state
        is FindingState.REPAIR_PROPOSED
    )

    assert (
        entry.repair_candidate_generation
        == 2
    )

    assert (
        entry.repair_candidate_identity_digest
        == F
    )

    assert (
        entry.repair_fixer_identity_digest
        == H
    )


def test_closure_requires_exact_repair_candidate():
    repaired = lifecycle().apply(
        repair_event(
            lifecycle()
        )
    )

    entry = repaired.entries[0]

    event = FindingTransitionEventV1(
        finding_id=entry.finding.finding_id,

        from_state=(
            FindingState.REPAIR_PROPOSED
        ),

        to_state=(
            FindingState.VERIFIED_CLOSED
        ),

        candidate_generation=3,
        candidate_identity_digest=A,

        actor_identity_envelope_digest=C,

        proof_kind=(
            FindingProofKind
            .INDEPENDENT_VERIFICATION
        ),

        evidence_digest=D,

        verification_set=(
            verification_set(
                generation=3,
                candidate_digest=A,
            )
        ),

        created_at=(
            "2026-08-30T05:20:00+00:00"
        ),
    )

    with pytest.raises(
        ValueError,
        match="exact repair candidate",
    ):
        repaired.apply(event)


def test_fixer_cannot_verify_own_repair():
    initial = lifecycle()

    repaired = initial.apply(
        repair_event(
            initial,
            fixer=H,
        )
    )

    event = close_event(
        repaired,
        verifiers=(H,),
    )

    with pytest.raises(
        ValueError,
        match="fixer cannot verify its own repair",
    ):
        repaired.apply(event)


def test_independent_verification_closes_repair():
    initial = lifecycle()

    repaired = initial.apply(
        repair_event(initial)
    )

    closed = repaired.apply(
        close_event(repaired)
    )

    assert (
        closed.entries[0].state
        is FindingState.VERIFIED_CLOSED
    )

    assert (
        closed.unresolved
        == ()
    )


def test_direct_open_to_closed_is_rejected():
    state = lifecycle()
    finding = state.entries[0].finding

    with pytest.raises(
        ValueError,
        match="transition is not allowed",
    ):
        FindingTransitionEventV1(
            finding_id=finding.finding_id,

            from_state=FindingState.OPEN,

            to_state=(
                FindingState.VERIFIED_CLOSED
            ),

            candidate_generation=1,

            candidate_identity_digest=E,

            actor_identity_envelope_digest=C,

            proof_kind=(
                FindingProofKind
                .INDEPENDENT_VERIFICATION
            ),

            evidence_digest=D,

            verification_set=(
                verification_set(
                    generation=1,
                    candidate_digest=E,
                )
            ),

            created_at=(
                "2026-08-30T05:20:00+00:00"
            ),
        )


def test_open_finding_can_be_independently_dismissed():
    state = lifecycle()
    finding = state.entries[0].finding

    event = FindingTransitionEventV1(
        finding_id=finding.finding_id,

        from_state=FindingState.OPEN,
        to_state=FindingState.DISMISSED,

        candidate_generation=1,

        candidate_identity_digest=E,

        actor_identity_envelope_digest=C,

        proof_kind=(
            FindingProofKind
            .INDEPENDENT_VERIFICATION
        ),

        evidence_digest=D,

        verification_set=(
            verification_set(
                generation=1,
                candidate_digest=E,
            )
        ),

        created_at=(
            "2026-08-30T05:20:00+00:00"
        ),
    )

    dismissed = state.apply(
        event
    )

    assert (
        dismissed.entries[0].state
        is FindingState.DISMISSED
    )

    assert dismissed.unresolved == ()


def test_closed_finding_can_reopen_on_deterministic_failure():
    initial = lifecycle()

    repaired = initial.apply(
        repair_event(initial)
    )

    closed = repaired.apply(
        close_event(repaired)
    )

    entry = closed.entries[0]

    reopen = FindingTransitionEventV1(
        finding_id=entry.finding.finding_id,

        from_state=(
            FindingState.VERIFIED_CLOSED
        ),

        to_state=FindingState.REOPENED,

        candidate_generation=3,

        candidate_identity_digest=A,

        actor_identity_envelope_digest=C,

        proof_kind=(
            FindingProofKind
            .DETERMINISTIC_FAILURE
        ),

        evidence_digest=D,

        created_at=(
            "2026-08-30T05:30:00+00:00"
        ),
    )

    reopened = closed.apply(
        reopen
    )

    assert (
        reopened.entries[0].state
        is FindingState.REOPENED
    )

    assert len(
        reopened.unresolved
    ) == 1

    assert (
        reopened.entries[0]
        .repair_candidate_generation
        is None
    )


def test_qualified_reopen_requires_verification_set():
    initial = lifecycle()

    repaired = initial.apply(
        repair_event(initial)
    )

    closed = repaired.apply(
        close_event(repaired)
    )

    entry = closed.entries[0]

    with pytest.raises(
        ValueError,
        match="requires verification set",
    ):
        FindingTransitionEventV1(
            finding_id=entry.finding.finding_id,

            from_state=(
                FindingState.VERIFIED_CLOSED
            ),

            to_state=FindingState.REOPENED,

            candidate_generation=3,

            candidate_identity_digest=A,

            actor_identity_envelope_digest=C,

            proof_kind=(
                FindingProofKind
                .QUALIFIED_INDEPENDENT_FINDING
            ),

            evidence_digest=D,

            created_at=(
                "2026-08-30T05:30:00+00:00"
            ),
        )


def test_transition_changes_lifecycle_digest():
    state = lifecycle()

    before = state.lifecycle_digest

    repaired = state.apply(
        repair_event(state)
    )

    assert (
        repaired.lifecycle_digest
        != before
    )


def test_finding_event_can_be_content_addressed_in_ledger():
    state = lifecycle()

    event = repair_event(
        state
    )

    outcome = (
        ReviewMeshLedgerV1()
        .append(
            record_type=(
                LedgerRecordType
                .FINDING_TRANSITION
            ),

            payload=(
                event.stable_mapping()
            ),

            related_task_id="g0a-slice4",

            related_request_id=None,

            related_campaign_id=None,

            actor_provenance_digest=C,

            ingestion_receipt_digest=D,

            idempotency_key=(
                "finding-transition-1"
            ),

            created_at=(
                "2026-08-30T05:10:01+00:00"
            ),
        )
    )

    assert (
        outcome.record.payload_digest
        == event.event_digest
    )


def test_duplicate_finding_transition_does_not_append_twice():
    state = lifecycle()

    event = repair_event(
        state
    )

    first = (
        ReviewMeshLedgerV1()
        .append(
            record_type=(
                LedgerRecordType
                .FINDING_TRANSITION
            ),

            payload=(
                event.stable_mapping()
            ),

            related_task_id="g0a-slice4",

            related_request_id=None,

            related_campaign_id=None,

            actor_provenance_digest=C,

            ingestion_receipt_digest=D,

            idempotency_key=(
                "finding-transition-1"
            ),

            created_at=(
                "2026-08-30T05:10:01+00:00"
            ),
        )
    )

    retry = (
        first.ledger.append(
            record_type=(
                LedgerRecordType
                .FINDING_TRANSITION
            ),

            payload=(
                event.stable_mapping()
            ),

            related_task_id="g0a-slice4",

            related_request_id=None,

            related_campaign_id=None,

            actor_provenance_digest=C,

            ingestion_receipt_digest=D,

            idempotency_key=(
                "finding-transition-1"
            ),

            created_at=(
                "2026-08-30T05:11:00+00:00"
            ),
        )
    )

    assert retry.duplicate is True

    assert len(
        retry.ledger.records
    ) == 1
