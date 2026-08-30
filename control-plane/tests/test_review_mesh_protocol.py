import pytest

from local_ai_control.services.review_mesh_protocol import (
    CampaignContextV1,
    ExecutionLocality,
    IdentityEnvelopeV1,
    PrivacyClass,
    ResultIngestionV1,
    ReviewerClass,
    ReviewRequestV1,
    ReviewResultPayloadV1,
    ReviewVerdict,
    RiskLevel,
    canonical_digest,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
E = "e" * 64
F = "f" * 64

BASE = "1" * 40
HEAD = "2" * 40
HEAD_2 = "3" * 40


def campaign(**changes):
    values = dict(
        repository_id=(
            "lxxlx2/local-ai-platform"
        ),
        task_id="g0a-protocol-test",
        source_work_unit_id="wu-source-1",

        review_round=1,
        candidate_generation=1,
        review_generation=1,

        objective_sha256=A,
        objective_manifest_hash=B,

        candidate_identity_digest=C,
        base_sha=BASE,
        candidate_sha=HEAD,
        candidate_diff_sha256=D,

        review_scope_manifest_digest=E,
        reviewed_material_digest=F,
        contributor_set_digest=A,
        local_gate_evidence_digest=B,

        policy_revision="review-policy-v1",
        policy_decision_digest=C,

        risk_level=RiskLevel.P1,
        risk_decision_digest=D,

        privacy_class=PrivacyClass.RESTRICTED,
        egress_decision_digest=E,
        privacy_decision_digest=F,

        required_reviewer_class=(
            ReviewerClass.STRONG_P1
        ),
        quorum_policy_digest=A,

        lineage_registry_snapshot_digest=B,
        qualification_registry_snapshot_digest=C,

        benchmark_harness_policy_revision=(
            "review-bench-v1"
        ),
        campaign_retry_policy_digest=D,
    )

    values.update(changes)

    return CampaignContextV1(
        **values
    )


def request(
    context,
    *,
    work_unit="review-wu-1",
    lane="reviewer-family-a",
    principal="adapter:reviewer-a",
    attempt=1,
    nonce="9" * 32,
    created="2026-08-30T05:00:00+00:00",
    expiry="2026-08-30T05:10:00+00:00",
):
    return ReviewRequestV1(
        campaign=context,
        review_work_unit_id=work_unit,
        reviewer_lane=lane,
        required_adapter_principal=principal,
        lane_attempt=attempt,
        request_nonce=nonce,
        created_at=created,
        request_expiry_at=expiry,
    )


def test_canonical_digest_is_deterministic():
    assert canonical_digest(
        {
            "b": 2,
            "a": 1,
        }
    ) == canonical_digest(
        {
            "a": 1,
            "b": 2,
        }
    )


def test_campaign_mapping_contains_no_lane_fields():
    context = campaign()

    mapping = context.stable_mapping()

    forbidden = {
        "review_campaign_id",
        "review_work_unit_id",
        "reviewer_lane",
        "reviewer_identity",
        "required_adapter_principal",
        "request_nonce",
        "lane_attempt",
        "created_at",
        "request_expiry_at",
    }

    assert not (
        forbidden
        & set(mapping)
    )


def test_two_reviewer_lanes_share_campaign_identity():
    context = campaign()

    first = request(
        context,
        lane="family-a",
        principal="adapter:a",
    )

    second = request(
        context,
        work_unit="review-wu-2",
        lane="family-b",
        principal="adapter:b",
        nonce="8" * 32,
    )

    assert (
        first.campaign.review_campaign_id
        == second.campaign.review_campaign_id
    )

    assert (
        first.request_digest
        != second.request_digest
    )


def test_campaign_id_is_not_self_referential():
    context = campaign()

    mapping = context.stable_mapping()

    assert (
        "review_campaign_id"
        not in mapping
    )

    assert (
        "campaign_context_digest"
        not in mapping
    )

    assert (
        context.review_campaign_id
        == (
            "rc1:"
            + canonical_digest(mapping)
        )
    )


def test_retry_preserves_campaign_and_generation():
    first = request(
        campaign()
    )

    retry = first.retry(
        review_work_unit_id="review-wu-2",
        request_nonce="7" * 32,
        created_at=(
            "2026-08-30T05:01:00+00:00"
        ),
        request_expiry_at=(
            "2026-08-30T05:11:00+00:00"
        ),
    )

    assert retry.lane_attempt == 2

    assert (
        retry.campaign
        == first.campaign
    )

    assert (
        retry.campaign.review_generation
        == first.campaign.review_generation
    )

    assert (
        retry.campaign.review_campaign_id
        == first.campaign.review_campaign_id
    )

    assert (
        retry.review_request_id
        != first.review_request_id
    )


def test_retry_preserves_reviewer_lane_identity():
    first = request(
        campaign()
    )

    retry = first.retry(
        review_work_unit_id="review-wu-2",
        request_nonce="7" * 32,
        created_at=(
            "2026-08-30T05:01:00+00:00"
        ),
        request_expiry_at=(
            "2026-08-30T05:11:00+00:00"
        ),
    )

    assert (
        retry.reviewer_lane
        == first.reviewer_lane
    )

    assert (
        retry.required_adapter_principal
        == first.required_adapter_principal
    )


def test_semantic_policy_change_creates_new_campaign():
    first = campaign()

    second = first.new_review_generation(
        privacy_class=PrivacyClass.PRIVATE,
        privacy_decision_digest="0" * 64,
    )

    assert (
        second.review_generation
        == first.review_generation + 1
    )

    assert (
        second.review_campaign_id
        != first.review_campaign_id
    )


def test_new_review_generation_cannot_reuse_counter():
    first = campaign()

    with pytest.raises(
        ValueError,
        match="advance review generation",
    ):
        first.new_review_generation(
            review_generation=1
        )


def test_new_candidate_advances_both_generations():
    first = campaign()

    second = first.new_candidate_generation(
        candidate_sha=HEAD_2,
        candidate_identity_digest="0" * 64,
        candidate_diff_sha256="1" * 64,
        contributor_set_digest="2" * 64,
    )

    assert (
        second.candidate_generation
        == first.candidate_generation + 1
    )

    assert (
        second.review_generation
        == first.review_generation + 1
    )

    assert (
        second.review_campaign_id
        != first.review_campaign_id
    )


def test_h1_h2_h1_does_not_revive_old_campaign():
    first = campaign()

    h2 = first.new_candidate_generation(
        candidate_sha=HEAD_2,
        candidate_identity_digest="0" * 64,
        candidate_diff_sha256="1" * 64,
        contributor_set_digest="2" * 64,
    )

    h1_again = h2.new_candidate_generation(
        candidate_sha=HEAD,
        candidate_identity_digest=C,
        candidate_diff_sha256=D,
        contributor_set_digest=A,
    )

    assert (
        h1_again.candidate_sha
        == first.candidate_sha
    )

    assert (
        h1_again.candidate_generation
        > first.candidate_generation
    )

    assert (
        h1_again.review_campaign_id
        != first.review_campaign_id
    )


def test_same_head_new_base_changes_campaign():
    first = campaign()

    changed = first.new_candidate_generation(
        candidate_sha=HEAD,
        candidate_identity_digest="0" * 64,
        candidate_diff_sha256=D,
        contributor_set_digest=A,
        base_sha="4" * 40,
    )

    assert (
        changed.candidate_sha
        == first.candidate_sha
    )

    assert (
        changed.base_sha
        != first.base_sha
    )

    assert (
        changed.review_campaign_id
        != first.review_campaign_id
    )


def test_request_rejects_short_nonce():
    with pytest.raises(
        ValueError,
        match="128 bits",
    ):
        request(
            campaign(),
            nonce="abc",
        )


def test_request_requires_expiry_after_creation():
    with pytest.raises(
        ValueError,
        match="expiry must be after",
    ):
        request(
            campaign(),
            created=(
                "2026-08-30T05:10:00+00:00"
            ),
            expiry=(
                "2026-08-30T05:00:00+00:00"
            ),
        )


def test_exact_lowercase_git_sha_is_required():
    with pytest.raises(
        ValueError,
        match="base SHA",
    ):
        campaign(
            base_sha="ABC"
        )


def test_generations_reject_bool():
    with pytest.raises(
        ValueError,
        match="candidate generation",
    ):
        campaign(
            candidate_generation=True
        )


def test_different_lane_request_is_same_campaign():
    context = campaign()

    first = request(
        context,
        lane="reviewer-a",
        principal="adapter:a",
    )

    second = request(
        context,
        work_unit="review-wu-b",
        lane="reviewer-b",
        principal="adapter:b",
        nonce="6" * 32,
    )

    assert (
        first.same_campaign_as(second)
        is True
    )


def test_semantically_new_campaign_is_not_same_campaign():
    first_context = campaign()

    second_context = (
        first_context.new_review_generation(
            local_gate_evidence_digest=(
                "0" * 64
            )
        )
    )

    first = request(
        first_context
    )

    second = request(
        second_context,
        work_unit="review-wu-2",
        nonce="6" * 32,
    )

    assert (
        first.same_campaign_as(second)
        is False
    )


def identity(
    review_request,
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
        foundation_lineage_class="google-gemini-3",
        foundation_revision="gemini-3.6-flash",

        hosted_copy_relationship="NONE",
        derivative_relationship="NONE",

        execution_locality=ExecutionLocality.REMOTE,
        actual_egress_destination="google-gemini-api",

        invocation_id="invocation-001",
        execution_receipt_digest="3" * 64,

        request_nonce=review_request.request_nonce,
        reviewed_material_digest=(
            review_request.campaign.reviewed_material_digest
        ),
        review_request_digest=(
            review_request.request_digest
        ),

        candidate_generation=(
            review_request.campaign.candidate_generation
        ),

        invocation_started_at=(
            "2026-08-30T05:02:00+00:00"
        ),
        invocation_completed_at=(
            "2026-08-30T05:02:03+00:00"
        ),

        privacy_decision_digest=(
            review_request.campaign.privacy_decision_digest
        ),

        lineage_registry_snapshot_digest=(
            review_request.campaign
            .lineage_registry_snapshot_digest
        ),
        qualification_registry_snapshot_digest=(
            review_request.campaign
            .qualification_registry_snapshot_digest
        ),
        qualification_evidence_digest="4" * 64,

        orchestrator_ingested_at=(
            "2026-08-30T05:02:04+00:00"
        ),
        authenticated_ingestion_receipt_digest=(
            "5" * 64
        ),
    )

    values.update(changes)

    return IdentityEnvelopeV1(
        **values
    )


def result_payload(
    review_request,
    reviewer_identity,
    **changes,
):
    values = dict(
        request=review_request,
        reviewer_identity=reviewer_identity,

        qualification_evidence_digest=(
            reviewer_identity.qualification_evidence_digest
        ),

        invocation_id=(
            reviewer_identity.invocation_id
        ),

        execution_nonce="6" * 32,

        execution_receipt_digest=(
            reviewer_identity.execution_receipt_digest
        ),

        claimed_verdict=ReviewVerdict.PASS,

        normalized_findings=(),

        invocation_completed_at=(
            reviewer_identity.invocation_completed_at
        ),

        raw_result_content_digest="7" * 64,
        raw_result_storage_ref="content:review-result-001",
    )

    values.update(changes)

    return ReviewResultPayloadV1(
        **values
    )


def ingestion(
    result,
    **changes,
):
    values = dict(
        result=result,

        orchestrator_ingested_at=(
            "2026-08-30T05:02:05+00:00"
        ),

        authenticated_ingestion_receipt_digest=(
            "8" * 64
        ),

        idempotency_key="result-ingestion-001",
    )

    values.update(changes)

    return ResultIngestionV1(
        **values
    )


def test_identity_envelope_is_self_digest_free():
    req = request(
        campaign()
    )

    observed = identity(req)

    mapping = observed.stable_mapping()

    assert (
        "identity_envelope_id"
        not in mapping
    )

    assert (
        "identity_envelope_digest"
        not in mapping
    )

    assert (
        observed.identity_envelope_id
        == (
            "rei1:"
            + canonical_digest(mapping)
        )
    )


def test_identity_binds_exact_request():
    req = request(
        campaign()
    )

    observed = identity(req)

    observed.validate_for_request(req)


def test_identity_rejects_request_replay():
    first = request(
        campaign()
    )

    second = first.retry(
        review_work_unit_id="review-wu-2",
        request_nonce="7" * 32,
        created_at=(
            "2026-08-30T05:01:00+00:00"
        ),
        request_expiry_at=(
            "2026-08-30T05:11:00+00:00"
        ),
    )

    observed = identity(first)

    with pytest.raises(
        ValueError,
        match="nonce mismatch",
    ):
        observed.validate_for_request(
            second
        )


def test_unexpected_actual_model_requires_explicit_fallback():
    req = request(
        campaign()
    )

    with pytest.raises(
        ValueError,
        match="explicit registered fallback",
    ):
        identity(
            req,
            actual_model_id="gemini-3.5-flash",
        )


def test_registered_fallback_is_observed_actual_identity():
    req = request(
        campaign()
    )

    observed = identity(
        req,
        actual_model_id="gemini-3.5-flash",
        fallback_model_id="gemini-3.5-flash",
        fallback_reason="provider-fallback",
        foundation_model="gemini-3.5-flash",
        foundation_revision="gemini-3.5-flash",
    )

    assert (
        observed.actual_model_id
        == "gemini-3.5-flash"
    )

    assert (
        observed.fallback_model_id
        == observed.actual_model_id
    )


def test_result_payload_binds_identity_and_request():
    req = request(
        campaign()
    )

    observed = identity(req)

    result = result_payload(
        req,
        observed,
    )

    mapping = result.stable_mapping()

    assert (
        mapping["request_digest"]
        == req.request_digest
    )

    assert (
        mapping[
            "reviewer_identity_envelope_digest"
        ]
        == observed.identity_envelope_digest
    )

    assert (
        mapping["candidate_generation"]
        == req.campaign.candidate_generation
    )


def test_result_digest_is_self_reference_free():
    req = request(
        campaign()
    )

    observed = identity(req)

    result = result_payload(
        req,
        observed,
    )

    mapping = result.stable_mapping()

    forbidden = {
        "review_result_id",
        "review_result_digest",
        "ledger_sequence",
        "ledger_record_digest",
        "trusted_ingestion_receipt",
    }

    assert not (
        forbidden
        & set(mapping)
    )

    assert (
        result.review_result_id
        == (
            "rrs1:"
            + canonical_digest(mapping)
        )
    )


def test_result_rejects_different_execution_receipt():
    req = request(
        campaign()
    )

    observed = identity(req)

    with pytest.raises(
        ValueError,
        match="execution receipt mismatch",
    ):
        result_payload(
            req,
            observed,
            execution_receipt_digest="0" * 64,
        )


def test_result_findings_are_canonicalized():
    req = request(
        campaign()
    )

    observed = identity(req)

    finding = {
        "scope": "WORKFLOW",
        "severity": "HIGH",
        "file": None,
        "evidence": "stale result can replay",
        "recommended_fix": "bind request digest",
    }

    result = result_payload(
        req,
        observed,
        claimed_verdict=ReviewVerdict.FAIL,
        normalized_findings=(
            finding,
        ),
    )

    assert (
        result.findings_digest
        == canonical_digest(
            [finding]
        )
    )


def test_result_rejects_unknown_finding_fields():
    req = request(
        campaign()
    )

    observed = identity(req)

    with pytest.raises(
        ValueError,
        match="finding fields",
    ):
        result_payload(
            req,
            observed,
            normalized_findings=({
                "scope": "WORKFLOW",
                "severity": "HIGH",
                "file": None,
                "evidence": "evidence",
                "recommended_fix": "fix",
                "counting": True,
            },),
        )


def test_ingestion_depends_on_result_digest_only():
    req = request(
        campaign()
    )

    observed = identity(req)

    result = result_payload(
        req,
        observed,
    )

    accepted = ingestion(
        result
    )

    mapping = accepted.stable_mapping()

    assert (
        mapping["review_result_digest"]
        == result.review_result_digest
    )

    assert (
        mapping["review_result_id"]
        == result.review_result_id
    )


def test_ingestion_has_no_ledger_back_reference():
    req = request(
        campaign()
    )

    observed = identity(req)

    result = result_payload(
        req,
        observed,
    )

    accepted = ingestion(
        result
    )

    mapping = accepted.stable_mapping()

    assert (
        "ledger_sequence"
        not in mapping
    )

    assert (
        "ledger_record_digest"
        not in mapping
    )

    assert (
        "previous_ledger_head_digest"
        not in mapping
    )


def test_result_digest_cannot_change_from_later_ingestion():
    req = request(
        campaign()
    )

    observed = identity(req)

    result = result_payload(
        req,
        observed,
    )

    digest_before = (
        result.review_result_digest
    )

    first = ingestion(
        result,
        idempotency_key="ingestion-a",
    )

    second = ingestion(
        result,
        idempotency_key="ingestion-b",
    )

    assert (
        result.review_result_digest
        == digest_before
    )

    assert (
        first.result.review_result_digest
        == second.result.review_result_digest
        == digest_before
    )

    assert (
        first.ingestion_payload_digest
        != second.ingestion_payload_digest
    )


def test_result_for_retry_must_use_retry_identity():
    first = request(
        campaign()
    )

    retry = first.retry(
        review_work_unit_id="review-wu-2",
        request_nonce="7" * 32,
        created_at=(
            "2026-08-30T05:01:00+00:00"
        ),
        request_expiry_at=(
            "2026-08-30T05:11:00+00:00"
        ),
    )

    old_identity = identity(first)

    with pytest.raises(
        ValueError,
        match="nonce mismatch",
    ):
        result_payload(
            retry,
            old_identity,
        )
