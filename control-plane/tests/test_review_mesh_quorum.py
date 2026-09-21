from dataclasses import replace

import pytest

from local_ai_control.services.review_mesh_protocol import (
    CampaignContextV1,
    ExecutionLocality,
    IdentityEnvelopeV1,
    PrivacyClass,
    ReviewerClass,
    ReviewRequestV1,
    ReviewResultPayloadV1,
    ReviewVerdict,
    RiskLevel,
)

from local_ai_control.services.review_mesh_decisions import (
    ActiveReviewBindingsV1,
    ContributorEntryV1,
    ContributorHistoryV1,
    ContributorRole,
    InheritedFindingSetV1,
    LineageApprovalState,
    LineageRegistryEntryV1,
    LineageRegistrySnapshotV1,
    MaterialFindingV1,
    ObservedIdentityFactsV1,
)

from local_ai_control.services.review_mesh_quorum import (
    QualificationEligibilityV1,
    QuorumPolicyV1,
    ReviewerCapacityState,
    ReviewMeshDecisionState,
    TrustedCheckStatus,
    TrustedOwnerGateInputsV1,
    evaluate_quorum,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
E = "e" * 64
F = "f" * 64

BASE = "1" * 40
HEAD = "2" * 40


def history():
    return ContributorHistoryV1((
        ContributorEntryV1(
            role=ContributorRole.PRODUCER,
            candidate_generation=1,

            identity_envelope_digest=A,
            invocation_id="producer-1",
            execution_receipt_digest=B,

            task_or_request_digest=C,

            input_candidate_identity_digest=None,
            output_candidate_identity_digest=D,

            contributed_at=(
                "2026-08-30T04:00:00+00:00"
            ),
        ),
    ))


def lineage_registry():
    return LineageRegistrySnapshotV1(
        policy_revision="lineage-v1",

        entries=(
            LineageRegistryEntryV1(
                provider_principal="openai",
                serving_backend="openai-api",
                actual_model_id="gpt-5.6-sol",

                foundation_model="gpt-5.6",
                foundation_revision="gpt-5.6-sol",
                foundation_lineage_class="openai-gpt5",

                correlation_group="openai-provider",

                approval_state=(
                    LineageApprovalState.APPROVED
                ),
            ),

            LineageRegistryEntryV1(
                provider_principal="google-gemini",
                serving_backend="google-gemini-api",
                actual_model_id="gemini-3.6-flash",

                foundation_model="gemini-3.6-flash",
                foundation_revision="gemini-3.6-flash",
                foundation_lineage_class="google-gemini3",

                correlation_group="google-provider",

                approval_state=(
                    LineageApprovalState.APPROVED
                ),
            ),

            LineageRegistryEntryV1(
                provider_principal="mistral",
                serving_backend="mistral-api",
                actual_model_id="mistral-large",

                foundation_model="mistral-large",
                foundation_revision="mistral-large-v1",
                foundation_lineage_class="mistral-large",

                correlation_group="mistral-provider",

                approval_state=(
                    LineageApprovalState.APPROVED
                ),
            ),
        ),
    )


def policy(
    *,
    risk=RiskLevel.P1,
    reviewer_class=ReviewerClass.STRONG_P1,
    families=2,
):
    return QuorumPolicyV1(
        policy_revision="quorum-v1",
        risk_level=risk,

        required_reviewer_class=(
            reviewer_class
        ),

        minimum_independent_families=(
            families
        ),
    )


def campaign(
    *,
    policy_obj=None,
):
    h = history()
    registry = lineage_registry()

    p = (
        policy_obj
        if policy_obj is not None
        else policy()
    )

    return CampaignContextV1(
        repository_id=(
            "lxxlx2/local-ai-platform"
        ),

        task_id="slice6-test",
        source_work_unit_id="source-1",

        review_round=1,
        candidate_generation=1,
        review_generation=1,

        objective_sha256=A,
        objective_manifest_hash=B,

        candidate_identity_digest=D,

        base_sha=BASE,
        candidate_sha=HEAD,
        candidate_diff_sha256=C,

        review_scope_manifest_digest=E,
        reviewed_material_digest=F,

        contributor_set_digest=(
            h.contributor_set_digest
        ),

        local_gate_evidence_digest=A,

        policy_revision="review-policy-v1",
        policy_decision_digest=B,

        risk_level=p.risk_level,
        risk_decision_digest=C,

        privacy_class=PrivacyClass.RESTRICTED,
        egress_decision_digest=D,
        privacy_decision_digest=E,

        required_reviewer_class=(
            p.required_reviewer_class
        ),

        quorum_policy_digest=(
            p.policy_digest
        ),

        lineage_registry_snapshot_digest=(
            registry.snapshot_digest
        ),

        qualification_registry_snapshot_digest=A,

        benchmark_harness_policy_revision=(
            "review-bench-v1"
        ),

        campaign_retry_policy_digest=B,
    )


def request(
    context,
    *,
    lane,
    adapter,
    nonce,
    work_unit,
):
    return ReviewRequestV1(
        campaign=context,

        review_work_unit_id=work_unit,
        reviewer_lane=lane,

        required_adapter_principal=(
            adapter
        ),

        lane_attempt=1,
        request_nonce=nonce,

        created_at=(
            "2026-08-30T05:00:00+00:00"
        ),

        request_expiry_at=(
            "2026-08-30T05:10:00+00:00"
        ),
    )


def reviewer_identity(
    req,
    *,
    provider,
    backend,
    model,
    foundation,
    revision,
    lineage,
    invocation,
    receipt,
    qualification_digest,
):
    return IdentityEnvelopeV1(
        authenticated_adapter_principal=(
            req.required_adapter_principal
        ),

        authentication_method="api-key-v1",
        credential_version="credential-v1",

        provider_principal=provider,
        provider_account_scope="account-default",

        requested_model_id=model,
        requested_endpoint="review-endpoint",

        actual_model_id=model,

        fallback_model_id=None,
        fallback_reason="NO_FALLBACK",

        serving_backend=backend,

        foundation_model=foundation,
        foundation_lineage_class=lineage,
        foundation_revision=revision,

        hosted_copy_relationship="NONE",
        derivative_relationship="NONE",

        execution_locality=ExecutionLocality.REMOTE,

        actual_egress_destination=backend,

        invocation_id=invocation,
        execution_receipt_digest=receipt,

        request_nonce=req.request_nonce,

        reviewed_material_digest=(
            req.campaign.reviewed_material_digest
        ),

        review_request_digest=(
            req.request_digest
        ),

        candidate_generation=(
            req.campaign.candidate_generation
        ),

        invocation_started_at=(
            "2026-08-30T05:02:00+00:00"
        ),

        invocation_completed_at=(
            "2026-08-30T05:02:03+00:00"
        ),

        privacy_decision_digest=(
            req.campaign.privacy_decision_digest
        ),

        lineage_registry_snapshot_digest=(
            req.campaign
            .lineage_registry_snapshot_digest
        ),

        qualification_registry_snapshot_digest=(
            req.campaign
            .qualification_registry_snapshot_digest
        ),

        qualification_evidence_digest=(
            qualification_digest
        ),

        orchestrator_ingested_at=(
            "2026-08-30T05:02:04+00:00"
        ),

        authenticated_ingestion_receipt_digest=(
            "9" * 64
        ),
    )


def result(
    req,
    identity,
    *,
    execution_nonce,
    verdict=ReviewVerdict.PASS,
):
    return ReviewResultPayloadV1(
        request=req,
        reviewer_identity=identity,

        qualification_evidence_digest=(
            identity.qualification_evidence_digest
        ),

        invocation_id=identity.invocation_id,

        execution_nonce=execution_nonce,

        execution_receipt_digest=(
            identity.execution_receipt_digest
        ),

        claimed_verdict=verdict,

        normalized_findings=(),

        invocation_completed_at=(
            identity.invocation_completed_at
        ),

        raw_result_content_digest="8" * 64,

        raw_result_storage_ref=(
            "content:"
            + identity.invocation_id
        ),
    )


def gemini_result(
    context,
):
    req = request(
        context,
        lane="gemini",
        adapter="adapter:gemini",
        nonce="1" * 32,
        work_unit="review-gemini",
    )

    identity = reviewer_identity(
        req,

        provider="google-gemini",
        backend="google-gemini-api",
        model="gemini-3.6-flash",

        foundation="gemini-3.6-flash",
        revision="gemini-3.6-flash",
        lineage="google-gemini3",

        invocation="gemini-invocation-1",
        receipt="2" * 64,
        qualification_digest="3" * 64,
    )

    return result(
        req,
        identity,
        execution_nonce="4" * 32,
    )


def mistral_result(
    context,
):
    req = request(
        context,
        lane="mistral",
        adapter="adapter:mistral",
        nonce="5" * 32,
        work_unit="review-mistral",
    )

    identity = reviewer_identity(
        req,

        provider="mistral",
        backend="mistral-api",
        model="mistral-large",

        foundation="mistral-large",
        revision="mistral-large-v1",
        lineage="mistral-large",

        invocation="mistral-invocation-1",
        receipt="6" * 64,
        qualification_digest="7" * 64,
    )

    return result(
        req,
        identity,
        execution_nonce="8" * 32,
    )


def qualification_for(
    review_result,
    *,
    reviewer_class=ReviewerClass.STRONG_P1,
    active=True,
):
    identity = (
        review_result.reviewer_identity
    )

    return QualificationEligibilityV1(
        qualification_evidence_digest=(
            identity.qualification_evidence_digest
        ),

        actual_model_id=(
            identity.actual_model_id
        ),

        foundation_lineage_class=(
            identity.foundation_lineage_class
        ),

        qualified_reviewer_class=(
            reviewer_class
        ),

        eligible_risk_levels=(
            RiskLevel.P1,
            RiskLevel.P2,
            RiskLevel.P3,
        ),

        protocol_revision=(
            "REVIEW_MESH_PROTOCOL_V1"
        ),

        benchmark_harness_policy_revision=(
            review_result.request.campaign
            .benchmark_harness_policy_revision
        ),

        qualification_registry_snapshot_digest=(
            review_result.request.campaign
            .qualification_registry_snapshot_digest
        ),

        active=active,
    )


def contributor_facts(
    registry,
):
    return {
        A: ObservedIdentityFactsV1(
            identity_envelope_digest=A,

            provider_principal="openai",
            serving_backend="openai-api",
            actual_model_id="gpt-5.6-sol",

            foundation_model="gpt-5.6",
            foundation_revision="gpt-5.6-sol",

            claimed_foundation_lineage_class=(
                "openai-gpt5"
            ),

            lineage_registry_snapshot_digest=(
                registry.snapshot_digest
            ),
        )
    }


def trusted(
    *,
    capacity=ReviewerCapacityState.AVAILABLE,
    ledger=TrustedCheckStatus.PASS,
    provenance=TrustedCheckStatus.PASS,
    gates=TrustedCheckStatus.PASS,
    security=TrustedCheckStatus.PASS,
    privacy=TrustedCheckStatus.PASS,
    fixer=TrustedCheckStatus.PASS,
):
    return TrustedOwnerGateInputsV1(
        ledger_continuity=ledger,

        contributor_provenance=(
            provenance
        ),

        deterministic_gates=gates,
        security_evidence=security,
        privacy_evidence=privacy,

        fixer_convergence_clear=fixer,

        reviewer_capacity=capacity,
    )


def fixture(
    *,
    policy_obj=None,
):
    p = (
        policy_obj
        if policy_obj is not None
        else policy()
    )

    context = campaign(
        policy_obj=p
    )

    h = history()
    registry = lineage_registry()

    active = ActiveReviewBindingsV1(
        campaign=context,

        contributor_history=h,
        lineage_registry=registry,

        qualification_registry_snapshot_digest=(
            context
            .qualification_registry_snapshot_digest
        ),
    )

    return (
        p,
        context,
        h,
        registry,
        active,
    )


def evaluate(
    results,
    *,
    policy_obj=None,
    qualifications=None,
    findings=None,
    trusted_inputs=None,
):
    (
        p,
        _,
        h,
        registry,
        active,
    ) = fixture(
        policy_obj=policy_obj
    )

    if qualifications is None:
        qualifications = {
            item.reviewer_identity
            .qualification_evidence_digest:
                qualification_for(item)
            for item in results
        }

    if findings is None:
        findings = (
            InheritedFindingSetV1()
        )

    if trusted_inputs is None:
        trusted_inputs = trusted()

    return evaluate_quorum(
        results=tuple(results),

        active=active,

        contributor_history=h,

        contributor_identities=(
            contributor_facts(
                registry
            )
        ),

        lineage_registry=registry,

        qualification_by_evidence_digest=(
            qualifications
        ),

        policy=p,

        inherited_findings=findings,

        trusted=trusted_inputs,
    )


def test_p1_policy_cannot_reduce_two_family_floor():
    downgraded = policy(
        families=1
    )

    (
        _,
        context,
        h,
        registry,
        active,
    ) = fixture(
        policy_obj=downgraded
    )

    decision = evaluate_quorum(
        results=(),

        active=active,
        contributor_history=h,

        contributor_identities=(
            contributor_facts(
                registry
            )
        ),

        lineage_registry=registry,

        qualification_by_evidence_digest={},

        policy=downgraded,

        inherited_findings=(
            InheritedFindingSetV1()
        ),

        trusted=trusted(),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .INVALID_POLICY_DOWNGRADE
    )


def test_one_p1_family_is_not_quorum():
    (
        p,
        context,
        _,
        _,
        _,
    ) = fixture()

    gemini = gemini_result(
        context
    )

    decision = evaluate(
        [gemini]
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .REVIEW_IN_PROGRESS
    )

    assert (
        len(
            decision
            .counted_lineage_classes
        )
        == 1
    )


def test_two_distinct_p1_families_reach_owner_gate():
    (
        _,
        context,
        _,
        _,
        _,
    ) = fixture()

    gemini = gemini_result(
        context
    )

    mistral = mistral_result(
        context
    )

    decision = evaluate(
        [
            gemini,
            mistral,
        ]
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .OWNER_GATE_READY
    )

    assert set(
        decision.counted_lineage_classes
    ) == {
        "google-gemini3",
        "mistral-large",
    }

    assert (
        decision.protected_action_authorized
        is False
    )


def test_owner_gate_ready_never_authorizes_merge():
    (
        _,
        context,
        _,
        _,
        _,
    ) = fixture()

    decision = evaluate([
        gemini_result(context),
        mistral_result(context),
    ])

    assert (
        decision.state
        is ReviewMeshDecisionState
        .OWNER_GATE_READY
    )

    assert (
        decision
        .stable_mapping()[
            "protected_action_authorized"
        ]
        is False
    )


def test_temporary_capacity_shortage_waits_only_when_otherwise_clean():
    decision = evaluate(
        [],

        trusted_inputs=trusted(
            capacity=(
                ReviewerCapacityState
                .TEMPORARILY_UNAVAILABLE
            )
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .WAITING_FOR_INDEPENDENT_REVIEW
    )


def test_ledger_ambiguity_is_not_misreported_as_capacity_wait():
    decision = evaluate(
        [],

        trusted_inputs=trusted(
            capacity=(
                ReviewerCapacityState
                .TEMPORARILY_UNAVAILABLE
            ),

            ledger=(
                TrustedCheckStatus.UNKNOWN
            ),
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )


def test_privacy_ambiguity_is_not_capacity_wait():
    decision = evaluate(
        [],

        trusted_inputs=trusted(
            capacity=(
                ReviewerCapacityState
                .TEMPORARILY_UNAVAILABLE
            ),

            privacy=(
                TrustedCheckStatus.UNKNOWN
            ),
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_PRIVACY_RECONCILIATION
    )


def test_unresolved_material_finding_blocks_quorum():
    (
        _,
        context,
        _,
        _,
        _,
    ) = fixture()

    gemini = gemini_result(
        context
    )

    mistral = mistral_result(
        context
    )

    content_digest = (
        "9" * 64
    )

    finding = MaterialFindingV1(
        finding_id=(
            MaterialFindingV1
            .compute_finding_id(
                originating_result_digest=(
                    "0" * 64
                ),

                finding_ordinal=1,

                finding_content_digest=(
                    content_digest
                ),
            )
        ),

        originating_result_digest=(
            "0" * 64
        ),

        finding_ordinal=1,
        finding_content_digest=(
            content_digest
        ),

        candidate_generation_introduced=1,

        scope="WORKFLOW",
        severity="HIGH",
        file=None,

        evidence="material defect",
        recommended_fix="repair",

    )

    findings = InheritedFindingSetV1(
        (
            finding,
        )
    )

    decision = evaluate(
        [
            gemini,
            mistral,
        ],

        findings=findings,
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_MATERIAL_FINDING
    )


def test_inactive_qualification_does_not_count():
    (
        _,
        context,
        _,
        _,
        _,
    ) = fixture()

    gemini = gemini_result(
        context
    )

    decision = evaluate(
        [gemini],

        qualifications={
            gemini.reviewer_identity
            .qualification_evidence_digest:
                qualification_for(
                    gemini,
                    active=False,
                )
        },
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .UNVERIFIED_IDENTITY
    )


def test_insufficient_reviewer_class_does_not_count():
    (
        _,
        context,
        _,
        _,
        _,
    ) = fixture()

    gemini = gemini_result(
        context
    )

    decision = evaluate(
        [gemini],

        qualifications={
            gemini.reviewer_identity
            .qualification_evidence_digest:
                qualification_for(
                    gemini,
                    reviewer_class=(
                        ReviewerClass.P2
                    ),
                )
        },
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .UNVERIFIED_IDENTITY
    )


def test_stale_old_generation_vote_is_non_counting():
    (
        p,
        context,
        h,
        registry,
        _,
    ) = fixture()

    old = gemini_result(
        context
    )

    newer = (
        context.new_review_generation(
            local_gate_evidence_digest=(
                "0" * 64
            )
        )
    )

    active = ActiveReviewBindingsV1(
        campaign=newer,

        contributor_history=h,
        lineage_registry=registry,

        qualification_registry_snapshot_digest=(
            newer
            .qualification_registry_snapshot_digest
        ),
    )

    decision = evaluate_quorum(
        results=(old,),

        active=active,

        contributor_history=h,

        contributor_identities=(
            contributor_facts(
                registry
            )
        ),

        lineage_registry=registry,

        qualification_by_evidence_digest={
            old.reviewer_identity
            .qualification_evidence_digest:
                qualification_for(old)
        },

        policy=p,

        inherited_findings=(
            InheritedFindingSetV1()
        ),

        trusted=trusted(),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .REVIEW_IN_PROGRESS
    )

    assert (
        decision.counted_result_digests
        == ()
    )

    assert any(
        item.reason == "result-stale"
        for item in decision.rejected_votes
    )


def test_exact_duplicate_result_does_not_add_vote():
    (
        _,
        context,
        _,
        _,
        _,
    ) = fixture()

    gemini = gemini_result(
        context
    )

    decision = evaluate(
        [
            gemini,
            gemini,
        ]
    )

    assert (
        len(
            decision.counted_result_digests
        )
        == 1
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .REVIEW_IN_PROGRESS
    )


def test_conflicting_reuse_of_invocation_is_untrusted():
    (
        _,
        context,
        _,
        _,
        _,
    ) = fixture()

    first = gemini_result(
        context
    )

    # Same invocation/receipt/request but a materially different result.
    second = replace(
        first,
        execution_nonce="f" * 32,
        raw_result_content_digest="0" * 64,
    )

    assert (
        first.review_result_digest
        != second.review_result_digest
    )

    decision = evaluate(
        [
            first,
            second,
        ]
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .UNTRUSTED_RESULT
    )


def test_same_foundation_family_cannot_fill_two_slots():
    (
        _,
        context,
        _,
        registry,
        _,
    ) = fixture()

    first = gemini_result(
        context
    )

    req = request(
        context,
        lane="gemini-second-provider",
        adapter="adapter:gemini-copy",
        nonce="b" * 32,
        work_unit="review-gemini-copy",
    )

    identity = reviewer_identity(
        req,

        # Deliberately different provider/backend but same
        # canonical foundation class.
        provider="google-gemini",
        backend="google-gemini-api",
        model="gemini-3.6-flash",

        foundation="gemini-3.6-flash",
        revision="gemini-3.6-flash",
        lineage="google-gemini3",

        invocation="gemini-invocation-2",
        receipt="c" * 64,
        qualification_digest="d" * 64,
    )

    second = result(
        req,
        identity,
        execution_nonce="e" * 32,
    )

    decision = evaluate(
        [
            first,
            second,
        ]
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .NON_INDEPENDENT
    )

    assert (
        len(
            decision.counted_lineage_classes
        )
        == 1
    )


def test_non_pass_vote_never_counts():
    (
        _,
        context,
        _,
        _,
        _,
    ) = fixture()

    gemini = gemini_result(
        context
    )

    failed = replace(
        gemini,
        claimed_verdict=(
            ReviewVerdict.FAIL
        ),
    )

    decision = evaluate(
        [failed]
    )

    assert (
        decision.counted_result_digests
        == ()
    )


def test_unknown_capacity_fails_closed():
    decision = evaluate(
        [],

        trusted_inputs=trusted(
            capacity=(
                ReviewerCapacityState.UNKNOWN
            )
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_REVIEW_CAPACITY_RECONCILIATION
    )
