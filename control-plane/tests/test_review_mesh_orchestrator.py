from dataclasses import replace
from types import SimpleNamespace

import pytest

from local_ai_control.services.supervisor_contracts import (
    CandidateIdentity,
)

from local_ai_control.services.supervisor_round2_common import (
    TaskObjective,
)

from local_ai_control.services.review_mesh_protocol import (
    ExecutionLocality,
    IdentityEnvelopeV1,
    PrivacyClass,
    ResultIngestionV1,
    ReviewerClass,
    ReviewResultPayloadV1,
    ReviewVerdict,
    RiskLevel,
)

from local_ai_control.services.review_mesh_decisions import (
    ContributorEntryV1,
    ContributorHistoryV1,
    ContributorRole,
    InheritedFindingSetV1,
    LineageApprovalState,
    LineageRegistryEntryV1,
    LineageRegistrySnapshotV1,
    ObservedIdentityFactsV1,
)

from local_ai_control.services.review_mesh_ledger_store import (
    ReviewMeshLedgerSnapshotV1,
)

from local_ai_control.services.review_mesh_quorum import (
    QualificationEligibilityV1,
    QuorumPolicyV1,
    ReviewerCapacityState,
    ReviewMeshDecisionState,
    TrustedCheckStatus,
)

from local_ai_control.services.review_mesh_orchestrator import (
    BoundReviewCampaignV1,
    CampaignBindingInputsV1,
    OrchestratorGateInputsV1,
    bind_review_task,
    build_review_request,
    evaluate_owner_gate,
    record_result_evidence,
    result_has_active_ledger_evidence,
)

from local_ai_control.services.review_mesh_protocol import (
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
TREE = "3" * 40


class FakeReviewSpec:
    def __init__(
        self,
        *,
        objective,
        candidate,
        validated,
    ):
        self.task_objective = objective
        self.candidate_identity = candidate

        self.objective_sha256 = (
            validated[
                "objective_sha256"
            ]
        )

        self.objective_manifest_hash = (
            validated[
                "objective_manifest_hash"
            ]
        )

        self._validated = (
            validated
        )

        self.validate_calls = 0

    def validate(self):
        self.validate_calls += 1

        return dict(
            self._validated
        )


def objective():
    return TaskObjective(
        goal="Review candidate safely.",

        acceptance_criteria=(
            "All deterministic gates pass.",
        ),

        constraints=(
            "No deployment.",
        ),

        expected_artifacts=(
            "review evidence",
        ),

        source_work_unit_id="source-1",
    )


def candidate(
    *,
    ref_type="COMMIT",
):
    if ref_type == "COMMIT":
        return CandidateIdentity.from_mapping({
            "candidate_ref_type":
                "COMMIT",

            "candidate_commit_sha":
                HEAD,

            "candidate_tree_sha":
                TREE,

            "base_commit_sha":
                BASE,

            "candidate_diff_sha256":
                C,

            "candidate_created_at":
                "2026-08-30T05:00:00+00:00",

            "candidate_paths": [
                "control-plane/example.py",
            ],
        })

    return CandidateIdentity.from_mapping({
        "candidate_ref_type":
            "TREE_MANIFEST",

        "candidate_commit_sha":
            None,

        "candidate_tree_sha":
            D,

        "base_commit_sha":
            BASE,

        "candidate_diff_sha256":
            C,

        "candidate_created_at":
            "2026-08-30T05:00:00+00:00",

        "candidate_paths": [
            "control-plane/example.py",
        ],
    })


def validated_mapping(
    task_objective,
):
    return {
        "repo_root":
            "/Users/jerson/AI",

        "allowed_paths": [
            "/Users/jerson/AI/control-plane",
        ],

        "read_only":
            True,

        "risk_level":
            "P1",

        "timeout_seconds":
            300.0,

        "model_role":
            "REVIEW",

        "expected_review_schema":
            {},

        "task_prompt_sha256":
            F,

        "safe_file_manifest": [
            {
                "path":
                    "control-plane/example.py",

                "sha256":
                    E,

                "size_bytes":
                    123,
            }
        ],

        "task_objective":
            task_objective.to_mapping(),

        "objective_sha256":
            A,

        "objective_manifest_hash":
            B,
    }


def quorum_policy():
    return QuorumPolicyV1(
        policy_revision="quorum-v1",

        risk_level=(
            RiskLevel.P1
        ),

        required_reviewer_class=(
            ReviewerClass.STRONG_P1
        ),

        minimum_independent_families=2,
    )


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

                foundation_lineage_class=(
                    "openai-gpt5"
                ),

                correlation_group="openai-provider",

                approval_state=(
                    LineageApprovalState.APPROVED
                ),
            ),

            LineageRegistryEntryV1(
                provider_principal="google-gemini",

                serving_backend=(
                    "google-gemini-api"
                ),

                actual_model_id=(
                    "gemini-3.6-flash"
                ),

                foundation_model=(
                    "gemini-3.6-flash"
                ),

                foundation_revision=(
                    "gemini-3.6-flash"
                ),

                foundation_lineage_class=(
                    "google-gemini3"
                ),

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

                foundation_revision=(
                    "mistral-large-v1"
                ),

                foundation_lineage_class=(
                    "mistral-large"
                ),

                correlation_group="mistral-provider",

                approval_state=(
                    LineageApprovalState.APPROVED
                ),
            ),
        ),
    )


def history_for_candidate(
    candidate_obj,
):
    digest = canonical_digest(
        candidate_obj.stable_payload()
    )

    return ContributorHistoryV1((
        ContributorEntryV1(
            role=(
                ContributorRole.PRODUCER
            ),

            candidate_generation=1,

            identity_envelope_digest=D,

            invocation_id="producer-1",

            execution_receipt_digest=E,

            task_or_request_digest=F,

            input_candidate_identity_digest=None,

            output_candidate_identity_digest=(
                digest
            ),

            contributed_at=(
                "2026-08-30T04:00:00+00:00"
            ),
        ),
    ))


def binding_inputs():
    return CampaignBindingInputsV1(
        repository_id=(
            "lxxlx2/local-ai-platform"
        ),

        task_id="g0a-slice7-test",

        source_work_unit_id="source-1",

        review_round=1,

        candidate_generation=1,

        review_generation=1,

        privacy_class=(
            PrivacyClass.RESTRICTED
        ),

        local_gate_evidence_digest=D,

        policy_revision="review-policy-v1",

        policy_decision_digest=E,

        risk_decision_digest=F,

        egress_decision_digest=A,

        privacy_decision_digest=B,

        qualification_registry_snapshot_digest=C,

        benchmark_harness_policy_revision=(
            "review-bench-v1"
        ),

        campaign_retry_policy_digest=D,
    )


def bound_fixture():
    task_objective = objective()

    candidate_obj = candidate()

    history = history_for_candidate(
        candidate_obj
    )

    registry = lineage_registry()

    validated = validated_mapping(
        task_objective
    )

    spec = FakeReviewSpec(
        objective=task_objective,

        candidate=candidate_obj,

        validated=validated,
    )

    policy = quorum_policy()

    bound = bind_review_task(
        spec=spec,

        bindings=binding_inputs(),

        contributor_history=history,

        lineage_registry=registry,

        quorum_policy=policy,
    )

    return (
        spec,
        candidate_obj,
        history,
        registry,
        policy,
        bound,
    )


def reviewer_request(
    bound,
    *,
    family,
):
    if family == "gemini":
        return build_review_request(
            bound,

            review_work_unit_id=(
                "review-gemini"
            ),

            reviewer_lane="gemini",

            required_adapter_principal=(
                "adapter:gemini"
            ),

            lane_attempt=1,

            request_nonce="1" * 32,

            created_at=(
                "2026-08-30T05:10:00+00:00"
            ),

            request_expiry_at=(
                "2026-08-30T05:20:00+00:00"
            ),
        )

    return build_review_request(
        bound,

        review_work_unit_id=(
            "review-mistral"
        ),

        reviewer_lane="mistral",

        required_adapter_principal=(
            "adapter:mistral"
        ),

        lane_attempt=1,

        request_nonce="2" * 32,

        created_at=(
            "2026-08-30T05:10:00+00:00"
        ),

        request_expiry_at=(
            "2026-08-30T05:20:00+00:00"
        ),
    )


def reviewer_result(
    bound,
    *,
    family,
):
    req = reviewer_request(
        bound,
        family=family,
    )

    if family == "gemini":
        provider = "google-gemini"
        backend = "google-gemini-api"
        model = "gemini-3.6-flash"
        foundation = "gemini-3.6-flash"
        revision = "gemini-3.6-flash"
        lineage = "google-gemini3"
        invocation = "gemini-invocation"
        receipt = "4" * 64
        qualification = "5" * 64
        execution_nonce = "6" * 32

    else:
        provider = "mistral"
        backend = "mistral-api"
        model = "mistral-large"
        foundation = "mistral-large"
        revision = "mistral-large-v1"
        lineage = "mistral-large"
        invocation = "mistral-invocation"
        receipt = "7" * 64
        qualification = "8" * 64
        execution_nonce = "9" * 32

    identity = IdentityEnvelopeV1(
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

        execution_locality=(
            ExecutionLocality.REMOTE
        ),

        actual_egress_destination=backend,

        invocation_id=invocation,

        execution_receipt_digest=receipt,

        request_nonce=req.request_nonce,

        reviewed_material_digest=(
            req.campaign
            .reviewed_material_digest
        ),

        review_request_digest=(
            req.request_digest
        ),

        candidate_generation=(
            req.campaign
            .candidate_generation
        ),

        invocation_started_at=(
            "2026-08-30T05:11:00+00:00"
        ),

        invocation_completed_at=(
            "2026-08-30T05:11:03+00:00"
        ),

        privacy_decision_digest=(
            req.campaign
            .privacy_decision_digest
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
            qualification
        ),

        orchestrator_ingested_at=(
            "2026-08-30T05:11:04+00:00"
        ),

        authenticated_ingestion_receipt_digest=(
            "a" * 64
        ),
    )

    result = ReviewResultPayloadV1(
        request=req,

        reviewer_identity=identity,

        qualification_evidence_digest=(
            qualification
        ),

        invocation_id=(
            invocation
        ),

        execution_nonce=(
            execution_nonce
        ),

        execution_receipt_digest=(
            receipt
        ),

        claimed_verdict=(
            ReviewVerdict.PASS
        ),

        normalized_findings=(),

        invocation_completed_at=(
            identity
            .invocation_completed_at
        ),

        raw_result_content_digest=(
            "b" * 64
        ),

        raw_result_storage_ref=(
            "content:"
            + invocation
        ),
    )

    ingestion = ResultIngestionV1(
        result=result,

        orchestrator_ingested_at=(
            "2026-08-30T05:11:05+00:00"
        ),

        authenticated_ingestion_receipt_digest=(
            "c" * 64
        ),

        idempotency_key=(
            "ingest-"
            + family
        ),
    )

    return (
        result,
        ingestion,
    )


def qualification(
    result,
):
    identity = (
        result.reviewer_identity
    )

    return QualificationEligibilityV1(
        qualification_evidence_digest=(
            identity
            .qualification_evidence_digest
        ),

        actual_model_id=(
            identity.actual_model_id
        ),

        foundation_lineage_class=(
            identity
            .foundation_lineage_class
        ),

        qualified_reviewer_class=(
            ReviewerClass.STRONG_P1
        ),

        eligible_risk_levels=(
            RiskLevel.P1,
        ),

        protocol_revision=(
            "REVIEW_MESH_PROTOCOL_V1"
        ),

        benchmark_harness_policy_revision=(
            result.request.campaign
            .benchmark_harness_policy_revision
        ),

        qualification_registry_snapshot_digest=(
            result.request.campaign
            .qualification_registry_snapshot_digest
        ),
    )


def contributor_facts(
    registry,
):
    return {
        D: ObservedIdentityFactsV1(
            identity_envelope_digest=D,

            provider_principal="openai",

            serving_backend="openai-api",

            actual_model_id="gpt-5.6-sol",

            foundation_model="gpt-5.6",

            foundation_revision="gpt-5.6-sol",

            claimed_foundation_lineage_class=(
                "openai-gpt5"
            ),

            lineage_registry_snapshot_digest=(
                registry
                .snapshot_digest
            ),
        )
    }


def clean_gate_inputs(
    *,
    capacity=ReviewerCapacityState.AVAILABLE,
):
    return OrchestratorGateInputsV1(
        deterministic_gates=(
            TrustedCheckStatus.PASS
        ),

        security_evidence=(
            TrustedCheckStatus.PASS
        ),

        privacy_evidence=(
            TrustedCheckStatus.PASS
        ),

        fixer_convergence_clear=(
            TrustedCheckStatus.PASS
        ),

        reviewer_capacity=capacity,
    )


def test_binding_calls_existing_spec_validator():
    (
        spec,
        _,
        _,
        _,
        _,
        _,
    ) = bound_fixture()

    assert spec.validate_calls == 1


def test_campaign_binds_objective_candidate_and_manifest():
    (
        _,
        candidate_obj,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    assert (
        bound.candidate_identity_digest
        == canonical_digest(
            candidate_obj.stable_payload()
        )
    )

    assert (
        bound.campaign
        .contributor_set_digest
        == history.contributor_set_digest
    )

    assert (
        bound.campaign
        .lineage_registry_snapshot_digest
        == registry.snapshot_digest
    )

    assert (
        bound.campaign
        .quorum_policy_digest
        == policy.policy_digest
    )

    assert (
        bound.campaign
        .review_scope_manifest_digest
        == bound.review_scope_manifest_digest
    )

    assert (
        bound.campaign
        .reviewed_material_digest
        == bound.reviewed_material_digest
    )


def test_tree_manifest_candidate_fails_closed():
    task_objective = objective()

    candidate_obj = candidate(
        ref_type="TREE_MANIFEST"
    )

    history = history_for_candidate(
        candidate_obj
    )

    registry = lineage_registry()

    spec = FakeReviewSpec(
        objective=task_objective,

        candidate=candidate_obj,

        validated=validated_mapping(
            task_objective
        ),
    )

    with pytest.raises(
        ValueError,
        match="requires committed candidate",
    ):
        bind_review_task(
            spec=spec,

            bindings=binding_inputs(),

            contributor_history=history,

            lineage_registry=registry,

            quorum_policy=quorum_policy(),
        )


def test_contributor_history_must_bind_exact_candidate():
    (
        _,
        candidate_obj,
        _,
        registry,
        policy,
        _,
    ) = bound_fixture()

    task_objective = objective()

    wrong_history = ContributorHistoryV1((
        ContributorEntryV1(
            role=ContributorRole.PRODUCER,

            candidate_generation=1,

            identity_envelope_digest=D,

            invocation_id="producer-1",

            execution_receipt_digest=E,

            task_or_request_digest=F,

            input_candidate_identity_digest=None,

            output_candidate_identity_digest=(
                "0" * 64
            ),

            contributed_at=(
                "2026-08-30T04:00:00+00:00"
            ),
        ),
    ))

    spec = FakeReviewSpec(
        objective=task_objective,

        candidate=candidate_obj,

        validated=validated_mapping(
            task_objective
        ),
    )

    with pytest.raises(
        ValueError,
        match="not bound to candidate",
    ):
        bind_review_task(
            spec=spec,

            bindings=binding_inputs(),

            contributor_history=wrong_history,

            lineage_registry=registry,

            quorum_policy=policy,
        )


def test_request_uses_exact_bound_campaign():
    (
        _,
        _,
        _,
        _,
        _,
        bound,
    ) = bound_fixture()

    req = reviewer_request(
        bound,
        family="gemini",
    )

    assert (
        req.campaign
        .campaign_context_digest
        == bound.campaign
        .campaign_context_digest
    )


def test_result_and_ingestion_append_as_two_ledger_records():
    (
        _,
        _,
        _,
        _,
        _,
        bound,
    ) = bound_fixture()

    result, ingestion = (
        reviewer_result(
            bound,
            family="gemini",
        )
    )

    recorded = (
        record_result_evidence(
            snapshot=(
                ReviewMeshLedgerSnapshotV1()
            ),

            bound=bound,

            result=result,

            ingestion=ingestion,
        )
    )

    assert (
        recorded.snapshot.record_count
        == 2
    )

    assert result_has_active_ledger_evidence(
        snapshot=recorded.snapshot,
        bound=bound,
        result=result,
    )


def test_result_evidence_replay_is_idempotent():
    (
        _,
        _,
        _,
        _,
        _,
        bound,
    ) = bound_fixture()

    result, ingestion = (
        reviewer_result(
            bound,
            family="gemini",
        )
    )

    first = record_result_evidence(
        snapshot=(
            ReviewMeshLedgerSnapshotV1()
        ),

        bound=bound,
        result=result,
        ingestion=ingestion,
    )

    second = record_result_evidence(
        snapshot=first.snapshot,

        bound=bound,
        result=result,
        ingestion=ingestion,
    )

    assert (
        second.snapshot
        == first.snapshot
    )

    assert (
        second.result_duplicate
        is True
    )

    assert (
        second.ingestion_duplicate
        is True
    )


def test_result_without_ingestion_blocks_owner_gate():
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    result, ingestion = (
        reviewer_result(
            bound,
            family="gemini",
        )
    )

    recorded = record_result_evidence(
        snapshot=(
            ReviewMeshLedgerSnapshotV1()
        ),

        bound=bound,
        result=result,
        ingestion=ingestion,
    )

    result_only_snapshot = (
        ReviewMeshLedgerSnapshotV1(
            recorded.snapshot
            .entries[:1]
        )
    )

    decision = evaluate_owner_gate(
        bound=bound,

        snapshot=(
            result_only_snapshot
        ),

        results=(result,),

        contributor_history=history,

        contributor_identities=(
            contributor_facts(
                registry
            )
        ),

        lineage_registry=registry,

        qualification_by_evidence_digest={
            result.reviewer_identity
            .qualification_evidence_digest:
                qualification(
                    result
                )
        },

        quorum_policy=policy,

        inherited_findings=(
            InheritedFindingSetV1()
        ),

        gate_inputs=(
            clean_gate_inputs()
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )


def test_missing_contributor_identity_blocks_before_capacity_wait():
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    decision = evaluate_owner_gate(
        bound=bound,

        snapshot=(
            ReviewMeshLedgerSnapshotV1()
        ),

        results=(),

        contributor_history=history,

        contributor_identities={},

        lineage_registry=registry,

        qualification_by_evidence_digest={},

        quorum_policy=policy,

        inherited_findings=(
            InheritedFindingSetV1()
        ),

        gate_inputs=(
            clean_gate_inputs(
                capacity=(
                    ReviewerCapacityState
                    .TEMPORARILY_UNAVAILABLE
                )
            )
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_IDENTITY_RECONCILIATION
    )


def test_clean_capacity_shortage_waits_for_review():
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    decision = evaluate_owner_gate(
        bound=bound,

        snapshot=(
            ReviewMeshLedgerSnapshotV1()
        ),

        results=(),

        contributor_history=history,

        contributor_identities=(
            contributor_facts(
                registry
            )
        ),

        lineage_registry=registry,

        qualification_by_evidence_digest={},

        quorum_policy=policy,

        inherited_findings=(
            InheritedFindingSetV1()
        ),

        gate_inputs=(
            clean_gate_inputs(
                capacity=(
                    ReviewerCapacityState
                    .TEMPORARILY_UNAVAILABLE
                )
            )
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .WAITING_FOR_INDEPENDENT_REVIEW
    )


def test_two_ledger_backed_independent_votes_reach_owner_gate():
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    gemini_result, gemini_ingestion = (
        reviewer_result(
            bound,
            family="gemini",
        )
    )

    mistral_result, mistral_ingestion = (
        reviewer_result(
            bound,
            family="mistral",
        )
    )

    snapshot = (
        ReviewMeshLedgerSnapshotV1()
    )

    first = record_result_evidence(
        snapshot=snapshot,

        bound=bound,

        result=gemini_result,

        ingestion=gemini_ingestion,
    )

    second = record_result_evidence(
        snapshot=first.snapshot,

        bound=bound,

        result=mistral_result,

        ingestion=mistral_ingestion,
    )

    qualifications = {
        gemini_result
        .reviewer_identity
        .qualification_evidence_digest:
            qualification(
                gemini_result
            ),

        mistral_result
        .reviewer_identity
        .qualification_evidence_digest:
            qualification(
                mistral_result
            ),
    }

    decision = evaluate_owner_gate(
        bound=bound,

        snapshot=(
            second.snapshot
        ),

        results=(
            gemini_result,
            mistral_result,
        ),

        contributor_history=history,

        contributor_identities=(
            contributor_facts(
                registry
            )
        ),

        lineage_registry=registry,

        qualification_by_evidence_digest=(
            qualifications
        ),

        quorum_policy=policy,

        inherited_findings=(
            InheritedFindingSetV1()
        ),

        gate_inputs=(
            clean_gate_inputs()
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .OWNER_GATE_READY
    )

    assert set(
        decision
        .counted_lineage_classes
    ) == {
        "google-gemini3",
        "mistral-large",
    }

    assert (
        decision
        .protected_action_authorized
        is False
    )


def test_cross_campaign_review_execution_reuse_is_untrusted():
    (
        _,
        _,
        history,
        registry,
        policy,
        first_bound,
    ) = bound_fixture()

    (
        first_result,
        first_ingestion,
    ) = reviewer_result(
        first_bound,
        family="gemini",
    )

    first_recorded = (
        record_result_evidence(
            snapshot=(
                ReviewMeshLedgerSnapshotV1()
            ),

            bound=first_bound,

            result=first_result,

            ingestion=first_ingestion,
        )
    )

    second_campaign = (
        first_bound
        .campaign
        .new_review_generation(
            local_gate_evidence_digest=(
                "0" * 64
            )
        )
    )

    second_bound = replace(
        first_bound,
        campaign=second_campaign,
    )

    (
        second_result,
        second_ingestion,
    ) = reviewer_result(
        second_bound,
        family="gemini",
    )

    # reviewer_result intentionally gives this second campaign
    # the same trusted invocation ID, execution nonce and receipt.
    # Its request/campaign/result digest is different, so this is
    # exactly the cross-campaign replay case.
    assert (
        second_result
        .review_result_digest
        != first_result
        .review_result_digest
    )

    assert (
        second_result.invocation_id
        == first_result.invocation_id
    )

    assert (
        second_result.execution_nonce
        == first_result.execution_nonce
    )

    assert (
        second_result
        .execution_receipt_digest
        == first_result
        .execution_receipt_digest
    )

    second_recorded = (
        record_result_evidence(
            snapshot=(
                first_recorded.snapshot
            ),

            bound=second_bound,

            result=second_result,

            ingestion=second_ingestion,
        )
    )

    decision = evaluate_owner_gate(
        bound=second_bound,

        snapshot=(
            second_recorded.snapshot
        ),

        results=(
            second_result,
        ),

        contributor_history=history,

        contributor_identities=(
            contributor_facts(
                registry
            )
        ),

        lineage_registry=registry,

        qualification_by_evidence_digest={
            second_result
            .reviewer_identity
            .qualification_evidence_digest:
                qualification(
                    second_result
                )
        },

        quorum_policy=policy,

        inherited_findings=(
            InheritedFindingSetV1()
        ),

        gate_inputs=(
            clean_gate_inputs()
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .UNTRUSTED_RESULT
    )

    assert (
        decision
        .protected_action_authorized
        is False
    )

    assert (
        len(
            decision.rejected_votes
        )
        == 1
    )

    assert (
        decision
        .rejected_votes[0]
        .reason
        == (
            "cross-campaign-"
            "invocation-id-reuse"
        )
    )
