import pytest

from local_ai_control.services.review_mesh_protocol import (
    CampaignContextV1,
    PrivacyClass,
    ReviewerClass,
    ReviewRequestV1,
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
