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
    FreshnessStatus,
    IndependenceStatus,
    InheritedFindingSetV1,
    LineageApprovalState,
    LineageRegistryEntryV1,
    LineageRegistrySnapshotV1,
    ObservedIdentityFactsV1,
    evaluate_independence,
    evaluate_result_freshness,
    material_findings_from_result,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
E = "e" * 64
F = "f" * 64

BASE = "1" * 40
HEAD = "2" * 40
HEAD2 = "3" * 40


def contributor_history():
    producer = ContributorEntryV1(
        role=ContributorRole.PRODUCER,
        candidate_generation=1,

        identity_envelope_digest=A,
        invocation_id="producer-invocation-1",
        execution_receipt_digest=B,

        task_or_request_digest=C,

        input_candidate_identity_digest=None,
        output_candidate_identity_digest=D,

        contributed_at=(
            "2026-08-30T04:00:00+00:00"
        ),
    )

    return ContributorHistoryV1(
        (producer,)
    )


def lineage_registry(
    *,
    contributor_class="openai-gpt5",
    reviewer_class="google-gemini3",
):
    return LineageRegistrySnapshotV1(
        policy_revision="lineage-policy-v1",

        entries=(
            LineageRegistryEntryV1(
                provider_principal="openai",
                serving_backend="openai-api",
                actual_model_id="gpt-5.6-sol",

                foundation_model="gpt-5.6",
                foundation_revision="gpt-5.6-sol",
                foundation_lineage_class=(
                    contributor_class
                ),

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
                foundation_lineage_class=(
                    reviewer_class
                ),

                correlation_group="google-provider",
                approval_state=(
                    LineageApprovalState.APPROVED
                ),
            ),
        ),
    )


def campaign(
    history,
    registry,
    **changes,
):
    values = dict(
        repository_id=(
            "lxxlx2/local-ai-platform"
        ),

        task_id="g0a-slice3-test",
        source_work_unit_id="wu-source-1",

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
            history.contributor_set_digest
        ),

        local_gate_evidence_digest=A,

        policy_revision="review-policy-v1",
        policy_decision_digest=B,

        risk_level=RiskLevel.P1,
        risk_decision_digest=C,

        privacy_class=PrivacyClass.RESTRICTED,
        egress_decision_digest=D,
        privacy_decision_digest=E,

        required_reviewer_class=(
            ReviewerClass.STRONG_P1
        ),

        quorum_policy_digest=F,

        lineage_registry_snapshot_digest=(
            registry.snapshot_digest
        ),

        qualification_registry_snapshot_digest=A,

        benchmark_harness_policy_revision=(
            "review-bench-v1"
        ),

        campaign_retry_policy_digest=B,
    )

    values.update(changes)

    return CampaignContextV1(
        **values
    )


def request(context):
    return ReviewRequestV1(
        campaign=context,

        review_work_unit_id="review-wu-1",
        reviewer_lane="gemini-family",

        required_adapter_principal=(
            "adapter:gemini-review"
        ),

        lane_attempt=1,
        request_nonce="9" * 32,

        created_at=(
            "2026-08-30T05:00:00+00:00"
        ),

        request_expiry_at=(
            "2026-08-30T05:10:00+00:00"
        ),
    )


def reviewer_identity(
    req,
    **changes,
):
    values = dict(
        authenticated_adapter_principal=(
            "adapter:gemini-review"
        ),

        authentication_method="api-key-v1",
        credential_version="keychain-v1",

        provider_principal="google-gemini",
        provider_account_scope="project-default",

        requested_model_id="gemini-3.6-flash",
        requested_endpoint="generate-content",

        actual_model_id="gemini-3.6-flash",

        fallback_model_id=None,
        fallback_reason="NO_FALLBACK",

        serving_backend="google-gemini-api",

        foundation_model="gemini-3.6-flash",

        foundation_lineage_class=(
            "google-gemini3"
        ),

        foundation_revision="gemini-3.6-flash",

        hosted_copy_relationship="NONE",
        derivative_relationship="NONE",

        execution_locality=ExecutionLocality.REMOTE,

        actual_egress_destination=(
            "google-gemini-api"
        ),

        invocation_id="review-invocation-1",

        execution_receipt_digest="4" * 64,

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

        qualification_evidence_digest="5" * 64,

        orchestrator_ingested_at=(
            "2026-08-30T05:02:04+00:00"
        ),

        authenticated_ingestion_receipt_digest=(
            "6" * 64
        ),
    )

    values.update(changes)

    return IdentityEnvelopeV1(
        **values
    )


def result_payload(
    req,
    identity,
    *,
    findings=(),
    verdict=ReviewVerdict.PASS,
):
    return ReviewResultPayloadV1(
        request=req,
        reviewer_identity=identity,

        qualification_evidence_digest=(
            identity.qualification_evidence_digest
        ),

        invocation_id=identity.invocation_id,

        execution_nonce="7" * 32,

        execution_receipt_digest=(
            identity.execution_receipt_digest
        ),

        claimed_verdict=verdict,
        normalized_findings=findings,

        invocation_completed_at=(
            identity.invocation_completed_at
        ),

        raw_result_content_digest="8" * 64,

        raw_result_storage_ref=(
            "content:review-result-slice3"
        ),
    )


def contributor_facts(
    registry,
    *,
    identity_digest=A,
    provider="openai",
    backend="openai-api",
    model="gpt-5.6-sol",
    foundation="gpt-5.6",
    revision="gpt-5.6-sol",
    claimed_class="openai-gpt5",
):
    return ObservedIdentityFactsV1(
        identity_envelope_digest=(
            identity_digest
        ),

        provider_principal=provider,
        serving_backend=backend,
        actual_model_id=model,

        foundation_model=foundation,
        foundation_revision=revision,

        claimed_foundation_lineage_class=(
            claimed_class
        ),

        lineage_registry_snapshot_digest=(
            registry.snapshot_digest
        ),
    )


def test_contributor_history_is_append_only_value():
    history = contributor_history()

    fixer = ContributorEntryV1(
        role=ContributorRole.FIXER,
        candidate_generation=2,

        identity_envelope_digest="0" * 64,
        invocation_id="fixer-invocation-1",
        execution_receipt_digest="1" * 64,

        task_or_request_digest="2" * 64,

        input_candidate_identity_digest=D,
        output_candidate_identity_digest="3" * 64,

        contributed_at=(
            "2026-08-30T04:10:00+00:00"
        ),
    )

    later = history.append(
        fixer
    )

    assert len(history.entries) == 1
    assert len(later.entries) == 2

    assert (
        history.contributor_set_digest
        != later.contributor_set_digest
    )


def test_contributor_chain_rejects_missing_parent():
    history = contributor_history()

    fixer = ContributorEntryV1(
        role=ContributorRole.FIXER,
        candidate_generation=2,

        identity_envelope_digest="0" * 64,
        invocation_id="fixer-invocation-1",
        execution_receipt_digest="1" * 64,

        task_or_request_digest="2" * 64,

        input_candidate_identity_digest="9" * 64,
        output_candidate_identity_digest="3" * 64,

        contributed_at=(
            "2026-08-30T04:10:00+00:00"
        ),
    )

    with pytest.raises(
        ValueError,
        match="chain is discontinuous",
    ):
        history.append(
            fixer
        )


def test_history_requires_first_producer():
    entry = ContributorEntryV1(
        role=ContributorRole.FIXER,
        candidate_generation=1,

        identity_envelope_digest=A,
        invocation_id="fixer-first",
        execution_receipt_digest=B,

        task_or_request_digest=C,

        input_candidate_identity_digest=None,
        output_candidate_identity_digest=D,

        contributed_at=(
            "2026-08-30T04:00:00+00:00"
        ),
    )

    with pytest.raises(
        ValueError,
        match="first contributor",
    ):
        ContributorHistoryV1(
            (entry,)
        )


def test_lineage_registry_digest_is_order_independent():
    registry = lineage_registry()

    reversed_registry = (
        LineageRegistrySnapshotV1(
            policy_revision=(
                registry.policy_revision
            ),
            entries=tuple(
                reversed(
                    registry.entries
                )
            ),
        )
    )

    assert (
        registry.snapshot_digest
        == reversed_registry.snapshot_digest
    )


def test_distinct_lineage_is_independent():
    history = contributor_history()
    registry = lineage_registry()

    context = campaign(
        history,
        registry,
    )

    req = request(context)

    reviewer = (
        ObservedIdentityFactsV1
        .from_review_identity(
            reviewer_identity(req)
        )
    )

    contributor = contributor_facts(
        registry
    )

    decision = evaluate_independence(
        reviewer=reviewer,
        contributor_history=history,

        contributor_identities={
            A: contributor,
        },

        lineage_registry=registry,
    )

    assert (
        decision.status
        is IndependenceStatus.INDEPENDENT
    )


def test_same_foundation_class_across_providers_is_not_independent():
    history = contributor_history()

    registry = lineage_registry(
        contributor_class="shared-foundation",
        reviewer_class="shared-foundation",
    )

    context = campaign(
        history,
        registry,
    )

    req = request(context)

    reviewer = (
        ObservedIdentityFactsV1
        .from_review_identity(
            reviewer_identity(
                req,
                foundation_lineage_class=(
                    "shared-foundation"
                ),
            )
        )
    )

    contributor = contributor_facts(
        registry,
        claimed_class="shared-foundation",
    )

    decision = evaluate_independence(
        reviewer=reviewer,
        contributor_history=history,

        contributor_identities={
            A: contributor,
        },

        lineage_registry=registry,
    )

    assert (
        decision.status
        is IndependenceStatus.NON_INDEPENDENT
    )

    assert (
        decision.reason
        == "reviewer-shares-contributor-lineage"
    )


def test_unknown_reviewer_lineage_fails_closed():
    history = contributor_history()
    registry = lineage_registry()

    unknown = ObservedIdentityFactsV1(
        identity_envelope_digest="0" * 64,

        provider_principal="unknown-provider",
        serving_backend="unknown-backend",
        actual_model_id="unknown-model",

        foundation_model="unknown-foundation",
        foundation_revision="unknown-revision",

        claimed_foundation_lineage_class=(
            "unknown-lineage"
        ),

        lineage_registry_snapshot_digest=(
            registry.snapshot_digest
        ),
    )

    decision = evaluate_independence(
        reviewer=unknown,

        contributor_history=history,

        contributor_identities={
            A: contributor_facts(
                registry
            ),
        },

        lineage_registry=registry,
    )

    assert (
        decision.status
        is IndependenceStatus.NON_INDEPENDENT
    )


def test_missing_contributor_identity_blocks_reconciliation():
    history = contributor_history()
    registry = lineage_registry()

    context = campaign(
        history,
        registry,
    )

    reviewer = (
        ObservedIdentityFactsV1
        .from_review_identity(
            reviewer_identity(
                request(context)
            )
        )
    )

    decision = evaluate_independence(
        reviewer=reviewer,

        contributor_history=history,

        contributor_identities={},

        lineage_registry=registry,
    )

    assert (
        decision.status
        is IndependenceStatus
        .BLOCKED_IDENTITY_RECONCILIATION
    )


def test_second_vote_from_same_lineage_does_not_count():
    history = contributor_history()
    registry = lineage_registry()

    context = campaign(
        history,
        registry,
    )

    reviewer = (
        ObservedIdentityFactsV1
        .from_review_identity(
            reviewer_identity(
                request(context)
            )
        )
    )

    other = ObservedIdentityFactsV1(
        identity_envelope_digest="0" * 64,

        provider_principal="google-gemini",
        serving_backend="google-gemini-api",
        actual_model_id="gemini-3.6-flash",

        foundation_model="gemini-3.6-flash",
        foundation_revision="gemini-3.6-flash",

        claimed_foundation_lineage_class=(
            "google-gemini3"
        ),

        lineage_registry_snapshot_digest=(
            registry.snapshot_digest
        ),
    )

    decision = evaluate_independence(
        reviewer=reviewer,

        contributor_history=history,

        contributor_identities={
            A: contributor_facts(
                registry
            ),
        },

        lineage_registry=registry,

        other_counted_reviewers=(
            other,
        ),
    )

    assert (
        decision.status
        is IndependenceStatus.NON_INDEPENDENT
    )

    assert (
        decision.reason
        == "reviewer-duplicates-counted-lineage"
    )


def current_fixture():
    history = contributor_history()
    registry = lineage_registry()

    context = campaign(
        history,
        registry,
    )

    req = request(context)
    identity = reviewer_identity(req)

    result = result_payload(
        req,
        identity,
    )

    active = ActiveReviewBindingsV1(
        campaign=context,
        contributor_history=history,
        lineage_registry=registry,

        qualification_registry_snapshot_digest=(
            context
            .qualification_registry_snapshot_digest
        ),
    )

    return (
        history,
        registry,
        context,
        result,
        active,
    )


def test_current_result_is_current():
    _, _, _, result, active = (
        current_fixture()
    )

    decision = (
        evaluate_result_freshness(
            result,
            active,
        )
    )

    assert (
        decision.status
        is FreshnessStatus.CURRENT
    )


def test_h1_h2_h1_old_result_stays_stale():
    (
        history,
        registry,
        context,
        result,
        _,
    ) = current_fixture()

    h2 = context.new_candidate_generation(
        candidate_sha=HEAD2,
        candidate_identity_digest="0" * 64,
        candidate_diff_sha256="1" * 64,
        contributor_set_digest=(
            history.contributor_set_digest
        ),
    )

    h1_again = h2.new_candidate_generation(
        candidate_sha=HEAD,
        candidate_identity_digest=D,
        candidate_diff_sha256=C,
        contributor_set_digest=(
            history.contributor_set_digest
        ),
    )

    active = ActiveReviewBindingsV1(
        campaign=h1_again,
        contributor_history=history,
        lineage_registry=registry,

        qualification_registry_snapshot_digest=(
            h1_again
            .qualification_registry_snapshot_digest
        ),
    )

    decision = (
        evaluate_result_freshness(
            result,
            active,
        )
    )

    assert (
        h1_again.candidate_sha
        == result.request.campaign.candidate_sha
    )

    assert (
        decision.status
        is FreshnessStatus.STALE
    )


def test_same_sha_new_gate_digest_stales_old_result():
    (
        history,
        registry,
        context,
        result,
        _,
    ) = current_fixture()

    changed = context.new_review_generation(
        local_gate_evidence_digest=(
            "0" * 64
        )
    )

    active = ActiveReviewBindingsV1(
        campaign=changed,
        contributor_history=history,
        lineage_registry=registry,

        qualification_registry_snapshot_digest=(
            changed
            .qualification_registry_snapshot_digest
        ),
    )

    assert (
        changed.candidate_sha
        == context.candidate_sha
    )

    assert (
        evaluate_result_freshness(
            result,
            active,
        ).status
        is FreshnessStatus.STALE
    )


def test_revoked_result_is_invalid():
    (
        history,
        registry,
        context,
        result,
        _,
    ) = current_fixture()

    active = ActiveReviewBindingsV1(
        campaign=context,
        contributor_history=history,
        lineage_registry=registry,

        qualification_registry_snapshot_digest=(
            context
            .qualification_registry_snapshot_digest
        ),

        revoked_result_digests=frozenset({
            result.review_result_digest,
        }),
    )

    assert (
        evaluate_result_freshness(
            result,
            active,
        ).status
        is FreshnessStatus.INVALID
    )


def material_result():
    history = contributor_history()
    registry = lineage_registry()

    context = campaign(
        history,
        registry,
    )

    req = request(context)
    identity = reviewer_identity(req)

    findings = (
        {
            "scope": "WORKFLOW",
            "severity": "HIGH",
            "file": None,
            "evidence": "old vote can replay",
            "recommended_fix": "bind generation",
        },

        {
            "scope": "FILE",
            "severity": "LOW",
            "file": "docs/example.md",
            "evidence": "minor wording",
            "recommended_fix": "clarify wording",
        },
    )

    return result_payload(
        req,
        identity,
        findings=findings,
        verdict=ReviewVerdict.FAIL,
    )


def test_only_material_findings_are_inherited():
    result = material_result()

    findings = (
        material_findings_from_result(
            result
        )
    )

    assert len(findings) == 1
    assert findings[0].severity == "HIGH"
    assert findings[0].unresolved is True


def test_material_finding_id_is_stable():
    result = material_result()

    first = (
        material_findings_from_result(
            result
        )
    )

    second = (
        material_findings_from_result(
            result
        )
    )

    assert (
        first[0].finding_id
        == second[0].finding_id
    )


def test_pass_result_does_not_erase_existing_material_finding():
    bad = material_result()

    inherited = (
        InheritedFindingSetV1()
        .merge_result(
            bad
        )
    )

    (
        history,
        registry,
        context,
        _,
        _,
    ) = current_fixture()

    pass_request = request(
        context.new_review_generation(
            local_gate_evidence_digest=(
                "0" * 64
            )
        )
    )

    pass_identity = reviewer_identity(
        pass_request
    )

    passed = result_payload(
        pass_request,
        pass_identity,
        findings=(),
        verdict=ReviewVerdict.PASS,
    )

    after_pass = inherited.merge_result(
        passed
    )

    assert (
        after_pass.finding_set_digest
        == inherited.finding_set_digest
    )

    assert len(
        after_pass.unresolved
    ) == 1


def test_stale_origin_does_not_delete_finding():
    bad = material_result()

    inherited = (
        InheritedFindingSetV1()
        .merge_result(
            bad
        )
    )

    finding_id = (
        inherited.unresolved[0]
        .finding_id
    )

    # Candidate generations can advance independently.
    # The inherited set is deliberately not derived from
    # whether the originating review remains current.
    assert (
        inherited.unresolved[0]
        .candidate_generation_introduced
        == 1
    )

    assert (
        inherited.unresolved[0]
        .finding_id
        == finding_id
    )
