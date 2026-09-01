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
    ReviewMeshLedgerAuthorityV1,
    ReviewMeshLedgerStoreV1,
    ReviewMeshLedgerSnapshotV1,
)

from local_ai_control.services.review_mesh_ledger import (
    LedgerRecordType,
    ReviewMeshLedgerV1,
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
    RESULT_LEDGER_SCHEMA,
    OrchestratorGateInputsV1,
    bind_review_task,
    build_review_request,
    evaluate_owner_gate,
    record_result_evidence,
    record_result_evidence_durably,
    result_has_active_ledger_evidence,
    review_result_ledger_payload,
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
    request_nonce=None,
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

            request_nonce=(
                "1" * 32
                if request_nonce is None
                else request_nonce
            ),

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

        request_nonce=(
            "2" * 32
            if request_nonce is None
            else request_nonce
        ),

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
    invocation_id=None,
    execution_nonce_override=None,
    execution_receipt_digest=None,
    request_nonce=None,
):
    req = reviewer_request(
        bound,
        family=family,
        request_nonce=request_nonce,
    )

    if family == "gemini":
        provider = "google-gemini"
        backend = "google-gemini-api"
        model = "gemini-3.6-flash"
        foundation = "gemini-3.6-flash"
        revision = "gemini-3.6-flash"
        lineage = "google-gemini3"
        invocation = (
            "gemini-invocation"
            if invocation_id is None
            else invocation_id
        )

        receipt = (
            "4" * 64
            if execution_receipt_digest is None
            else execution_receipt_digest
        )

        qualification = "5" * 64

        execution_nonce = (
            "6" * 32
            if execution_nonce_override is None
            else execution_nonce_override
        )

    else:
        provider = "mistral"
        backend = "mistral-api"
        model = "mistral-large"
        foundation = "mistral-large"
        revision = "mistral-large-v1"
        lineage = "mistral-large"
        invocation = (
            "mistral-invocation"
            if invocation_id is None
            else invocation_id
        )

        receipt = (
            "7" * 64
            if execution_receipt_digest is None
            else execution_receipt_digest
        )

        qualification = "8" * 64

        execution_nonce = (
            "9" * 32
            if execution_nonce_override is None
            else execution_nonce_override
        )

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


def authoritative_ledger(
    tmp_path,
    *,
    name="review-mesh-authority.json",
):
    store = ReviewMeshLedgerStoreV1(
        tmp_path / name
    )

    authority = store.initialize_authority(
        authority_id="g0a-test-authority"
    )

    return store, authority


def clean_gate_inputs(
    authority,
    *,
    capacity=ReviewerCapacityState.AVAILABLE,
):
    return OrchestratorGateInputsV1(
        ledger_authority_identity_digest=(
            authority.store_identity_digest
        ),
        ledger_authority_checkpoint_digest=(
            authority.checkpoint_digest
        ),

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


def owner_gate_decision(
    *,
    bound,
    store,
    authority,
    trusted_authority,
    results,
    history,
    registry,
    policy,
):
    return evaluate_owner_gate(
        bound=bound,

        ledger_store=store,
        ledger_authority=authority,

        results=results,

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
                qualification(result)
            for result in results
        },

        quorum_policy=policy,

        inherited_findings=(
            InheritedFindingSetV1()
        ),

        gate_inputs=(
            clean_gate_inputs(
                trusted_authority
            )
        ),
    )


def next_review_campaign(
    bound,
):
    return replace(
        bound,
        campaign=(
            bound.campaign
            .new_review_generation(
                local_gate_evidence_digest=(
                    "0" * 64
                )
            )
        ),
    )


def record_ready_quorum(
    *,
    store,
    authority,
    bound,
):
    gemini = reviewer_result(
        bound,
        family="gemini",
    )

    mistral = reviewer_result(
        bound,
        family="mistral",
    )

    first = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=authority,
        bound=bound,
        result=gemini[0],
        ingestion=gemini[1],
    )

    second = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=first.authority,
        bound=bound,
        result=mistral[0],
        ingestion=mistral[1],
    )

    return (
        second,
        (
            gemini[0],
            mistral[0],
        ),
    )


def append_historical_result_mapping(
    *,
    store,
    authority,
    result,
    inner,
    mutate_outer=None,
):
    digest = canonical_digest(
        inner
    )

    payload = {
        "schema_version":
            RESULT_LEDGER_SCHEMA,

        "review_result_id":
            "rrs1:" + digest,

        "review_result_digest":
            digest,

        "review_result_payload":
            inner,
    }

    if mutate_outer is not None:
        mutate_outer(
            payload
        )

    return store.append_authoritatively(
        authority=authority,

        record_type=(
            LedgerRecordType.REVIEW_RESULT
        ),

        payload=payload,

        related_task_id=(
            result.request.campaign.task_id
        ),

        related_request_id=(
            result.request.review_request_id
        ),

        related_campaign_id=(
            result.request.campaign.review_campaign_id
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
            "historical-result:"
            + digest
        ),

        created_at=(
            result.invocation_completed_at
        ),
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


def test_result_without_ingestion_blocks_owner_gate(
    tmp_path,
):
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

    store, authority = (
        authoritative_ledger(
            tmp_path
        )
    )

    result_only = store.append_authoritatively(
        authority=authority,

        record_type=(
            LedgerRecordType.REVIEW_RESULT
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
            result.request.review_request_id
        ),

        related_campaign_id=(
            bound.campaign.review_campaign_id
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
            result.invocation_completed_at
        ),
    )

    decision = evaluate_owner_gate(
        bound=bound,

        ledger_store=store,

        ledger_authority=(
            result_only.authority
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
            clean_gate_inputs(
                result_only.authority
            )
        ),
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )


def test_missing_contributor_identity_blocks_before_capacity_wait(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    decision = evaluate_owner_gate(
        bound=bound,

        ledger_store=store,

        ledger_authority=authority,

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
                authority,

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


def test_clean_capacity_shortage_waits_for_review(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    decision = evaluate_owner_gate(
        bound=bound,

        ledger_store=store,

        ledger_authority=authority,

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
                authority,

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


def test_two_ledger_backed_independent_votes_reach_owner_gate(
    tmp_path,
):
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

    store, authority = (
        authoritative_ledger(
            tmp_path
        )
    )

    first = record_result_evidence_durably(
        ledger_store=store,

        ledger_authority=authority,

        bound=bound,

        result=gemini_result,

        ingestion=gemini_ingestion,
    )

    second = record_result_evidence_durably(
        ledger_store=store,

        ledger_authority=first.authority,

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

        ledger_store=store,

        ledger_authority=(
            second.authority
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
            clean_gate_inputs(
                second.authority
            )
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


def test_cross_campaign_review_execution_reuse_is_untrusted(
    tmp_path,
):
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

    store, authority = (
        authoritative_ledger(
            tmp_path
        )
    )

    first_recorded = (
        record_result_evidence_durably(
            ledger_store=store,

            ledger_authority=authority,

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
        record_result_evidence_durably(
            ledger_store=store,

            ledger_authority=(
                first_recorded.authority
            ),

            bound=second_bound,

            result=second_result,

            ingestion=second_ingestion,
        )
    )

    decision = evaluate_owner_gate(
        bound=second_bound,

        ledger_store=store,

        ledger_authority=(
            second_recorded.authority
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
            clean_gate_inputs(
                second_recorded.authority
            )
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


def test_fresh_genesis_checkpoint_substitution_is_blocked(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        first_bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    first_result, first_ingestion = reviewer_result(
        first_bound,
        family="gemini",
    )

    first_recorded = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=authority,
        bound=first_bound,
        result=first_result,
        ingestion=first_ingestion,
    )

    trusted_authority = first_recorded.authority

    assert trusted_authority.trusted_record_count == 2

    # Simulate replacement/rollback of the durable history with a fresh,
    # structurally valid genesis at the exact same canonical store path.
    #
    # Reusing the same authority_id intentionally preserves store identity.
    store._atomic_write(
        ReviewMeshLedgerSnapshotV1()
    )

    forged_genesis_authority = store.initialize_authority(
        authority_id=trusted_authority.authority_id
    )

    assert (
        forged_genesis_authority.store_identity_digest
        == trusted_authority.store_identity_digest
    )
    assert (
        forged_genesis_authority.checkpoint_digest
        != trusted_authority.checkpoint_digest
    )

    second_bound = next_review_campaign(
        first_bound
    )

    second_gemini_result, second_gemini_ingestion = (
        reviewer_result(
            second_bound,
            family="gemini",
        )
    )

    # The helper deliberately reuses Gemini's trusted execution identity.
    assert (
        second_gemini_result.invocation_id
        == first_result.invocation_id
    )
    assert (
        second_gemini_result.execution_nonce
        == first_result.execution_nonce
    )
    assert (
        second_gemini_result.execution_receipt_digest
        == first_result.execution_receipt_digest
    )

    second_mistral_result, second_mistral_ingestion = (
        reviewer_result(
            second_bound,
            family="mistral",
        )
    )

    second_first = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=forged_genesis_authority,
        bound=second_bound,
        result=second_gemini_result,
        ingestion=second_gemini_ingestion,
    )

    second_recorded = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=second_first.authority,
        bound=second_bound,
        result=second_mistral_result,
        ingestion=second_mistral_ingestion,
    )

    # trusted_authority represents the Owner-private checkpoint that existed
    # before the durable ledger was replaced. A fresh-genesis authority must
    # not be accepted merely because its store identity is unchanged.
    decision = owner_gate_decision(
        bound=second_bound,
        store=store,
        authority=second_recorded.authority,
        trusted_authority=trusted_authority,
        results=(
            second_gemini_result,
            second_mistral_result,
        ),
        history=history,
        registry=registry,
        policy=policy,
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )

    assert (
        decision.protected_action_authorized
        is False
    )


def test_stale_authority_checkpoint_against_newer_store_is_blocked(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    recorded, results = record_ready_quorum(
        store=store,
        authority=authority,
        bound=bound,
    )

    decision = owner_gate_decision(
        bound=bound,
        store=store,
        authority=authority,
        trusted_authority=recorded.authority,
        results=results,
        history=history,
        registry=registry,
        policy=policy,
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )
    assert decision.protected_action_authorized is False


def test_valid_old_prefix_rollback_is_blocked(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    gemini_result, gemini_ingestion = reviewer_result(
        bound,
        family="gemini",
    )

    prefix = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=authority,
        bound=bound,
        result=gemini_result,
        ingestion=gemini_ingestion,
    )

    mistral_result, mistral_ingestion = reviewer_result(
        bound,
        family="mistral",
    )

    authoritative = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=prefix.authority,
        bound=bound,
        result=mistral_result,
        ingestion=mistral_ingestion,
    )

    assert prefix.snapshot.ledger.verify_continuity()
    assert (
        prefix.snapshot.record_count
        < authoritative.snapshot.record_count
    )

    store._atomic_write(
        prefix.snapshot
    )

    decision = owner_gate_decision(
        bound=bound,
        store=store,
        authority=authoritative.authority,
        trusted_authority=authoritative.authority,
        results=(
            gemini_result,
            mistral_result,
        ),
        history=history,
        registry=registry,
        policy=policy,
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )
    assert decision.protected_action_authorized is False


def test_same_length_valid_fork_is_blocked(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    gemini_result, gemini_ingestion = reviewer_result(
        bound,
        family="gemini",
    )

    prefix = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=authority,
        bound=bound,
        result=gemini_result,
        ingestion=gemini_ingestion,
    )

    mistral_result, mistral_ingestion = reviewer_result(
        bound,
        family="mistral",
    )

    authoritative = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=prefix.authority,
        bound=bound,
        result=mistral_result,
        ingestion=mistral_ingestion,
    )

    # Restore a real verified prefix, then extend it with different valid
    # records to create a locally continuous fork of the same final length.
    store._atomic_write(
        prefix.snapshot
    )

    fork_result, fork_ingestion = reviewer_result(
        bound,
        family="mistral",
        invocation_id="mistral-fork-invocation",
        execution_nonce_override="d" * 32,
        execution_receipt_digest="e" * 64,
        request_nonce="f" * 32,
    )

    forked = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=prefix.authority,
        bound=bound,
        result=fork_result,
        ingestion=fork_ingestion,
    )

    assert forked.snapshot.ledger.verify_continuity()
    assert authoritative.snapshot.ledger.verify_continuity()
    assert (
        forked.authority.trusted_record_count
        == authoritative.authority.trusted_record_count
    )
    assert (
        forked.authority.trusted_head_digest
        != authoritative.authority.trusted_head_digest
    )

    decision = owner_gate_decision(
        bound=bound,
        store=store,
        # The Owner-private authority still pins the original Hn. The store
        # now contains a same-length, structurally valid divergent Hn'.
        authority=authoritative.authority,
        trusted_authority=authoritative.authority,
        results=(
            gemini_result,
            mistral_result,
        ),
        history=history,
        registry=registry,
        policy=policy,
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )
    assert decision.protected_action_authorized is False


def test_wrong_canonical_store_path_is_blocked(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path,
        name="authoritative.json",
    )

    recorded, results = record_ready_quorum(
        store=store,
        authority=authority,
        bound=bound,
    )

    other_store = ReviewMeshLedgerStoreV1(
        tmp_path / "different-path.json"
    )

    other_store._atomic_write(
        recorded.snapshot
    )

    decision = owner_gate_decision(
        bound=bound,
        store=other_store,
        authority=recorded.authority,
        trusted_authority=recorded.authority,
        results=results,
        history=history,
        registry=registry,
        policy=policy,
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )
    assert decision.protected_action_authorized is False


def test_restart_reload_preserves_authoritative_owner_gate_decision(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    recorded, results = record_ready_quorum(
        store=store,
        authority=authority,
        bound=bound,
    )

    before = owner_gate_decision(
        bound=bound,
        store=store,
        authority=recorded.authority,
        trusted_authority=recorded.authority,
        results=results,
        history=history,
        registry=registry,
        policy=policy,
    )

    persisted_checkpoint = (
        recorded.authority.to_mapping()
    )

    restarted_store = ReviewMeshLedgerStoreV1(
        store.path
    )

    restarted_authority = (
        ReviewMeshLedgerAuthorityV1
        .from_mapping(
            persisted_checkpoint
        )
    )

    loaded = restarted_store.load_authoritative(
        restarted_authority
    )

    after = owner_gate_decision(
        bound=bound,
        store=restarted_store,
        authority=restarted_authority,
        trusted_authority=restarted_authority,
        results=results,
        history=history,
        registry=registry,
        policy=policy,
    )

    assert loaded.head_digest == recorded.snapshot.head_digest
    assert loaded.record_count == recorded.snapshot.record_count
    assert before.state is ReviewMeshDecisionState.OWNER_GATE_READY
    assert after.state is ReviewMeshDecisionState.OWNER_GATE_READY
    assert after.decision_digest == before.decision_digest
    assert after.protected_action_authorized is False


def test_cross_campaign_review_result_digest_reuse_is_untrusted(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        first_bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    first_result, first_ingestion = reviewer_result(
        first_bound,
        family="gemini",
    )

    recorded = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=authority,
        bound=first_bound,
        result=first_result,
        ingestion=first_ingestion,
    )

    second_bound = next_review_campaign(
        first_bound
    )

    decision = owner_gate_decision(
        bound=second_bound,
        store=store,
        authority=recorded.authority,
        trusted_authority=recorded.authority,
        # Replaying the whole old typed result is the only semantically valid
        # way to reuse its digest, because the digest itself binds campaign.
        results=(first_result,),
        history=history,
        registry=registry,
        policy=policy,
    )

    assert decision.state is ReviewMeshDecisionState.UNTRUSTED_RESULT
    assert decision.counted_result_digests == ()
    assert len(decision.rejected_votes) == 1
    assert (
        decision.rejected_votes[0].reason
        == "cross-campaign-review-result-digest-reuse"
    )
    assert decision.protected_action_authorized is False


@pytest.mark.parametrize(
    ("reuse_kind", "expected_reason"),
    (
        (
            "invocation_id",
            "cross-campaign-invocation-id-reuse",
        ),
        (
            "execution_nonce",
            "cross-campaign-execution-nonce-reuse",
        ),
        (
            "execution_receipt_digest",
            "cross-campaign-execution-receipt-reuse",
        ),
    ),
)
def test_cross_campaign_execution_identity_reuse_isolated(
    tmp_path,
    reuse_kind,
    expected_reason,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        first_bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    first_result, first_ingestion = reviewer_result(
        first_bound,
        family="gemini",
    )

    first_recorded = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=authority,
        bound=first_bound,
        result=first_result,
        ingestion=first_ingestion,
    )

    second_bound = next_review_campaign(
        first_bound
    )

    second_values = {
        "invocation_id":
            "gemini-invocation-2",

        "execution_nonce_override":
            "d" * 32,

        "execution_receipt_digest":
            "e" * 64,

        "request_nonce":
            "f" * 32,
    }

    if reuse_kind == "invocation_id":
        second_values["invocation_id"] = (
            first_result.invocation_id
        )
    elif reuse_kind == "execution_nonce":
        second_values["execution_nonce_override"] = (
            first_result.execution_nonce
        )
    else:
        second_values["execution_receipt_digest"] = (
            first_result.execution_receipt_digest
        )

    second_result, second_ingestion = reviewer_result(
        second_bound,
        family="gemini",
        **second_values,
    )

    assert second_result.review_result_digest != first_result.review_result_digest

    second_recorded = record_result_evidence_durably(
        ledger_store=store,
        ledger_authority=first_recorded.authority,
        bound=second_bound,
        result=second_result,
        ingestion=second_ingestion,
    )

    decision = owner_gate_decision(
        bound=second_bound,
        store=store,
        authority=second_recorded.authority,
        trusted_authority=second_recorded.authority,
        results=(second_result,),
        history=history,
        registry=registry,
        policy=policy,
    )

    assert decision.state is ReviewMeshDecisionState.UNTRUSTED_RESULT
    assert decision.counted_result_digests == ()
    assert len(decision.rejected_votes) == 1
    assert decision.rejected_votes[0].reason == expected_reason
    assert decision.protected_action_authorized is False


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        (
            "ledger_authority_identity_digest",
            True,
        ),
        (
            "ledger_authority_checkpoint_digest",
            1,
        ),
        (
            "ledger_authority_identity_digest",
            "0" * 63,
        ),
        (
            "ledger_authority_checkpoint_digest",
            "A" * 64,
        ),
    ),
)
def test_gate_authority_digests_require_exact_lowercase_sha256(
    tmp_path,
    field,
    invalid,
):
    _, authority = authoritative_ledger(
        tmp_path
    )

    trusted = clean_gate_inputs(
        authority
    )

    with pytest.raises(
        ValueError,
        match="ledger authority .* digest is invalid",
    ):
        replace(
            trusted,
            **{field: invalid},
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "outer-missing-field",
        "outer-unknown-field",
        "outer-wrong-primitive",
        "inner-missing-field",
        "inner-unknown-field",
        "inner-wrong-primitive",
        "nested-finding-wrong-type",
    ),
)
def test_malformed_historical_review_result_fails_closed(
    tmp_path,
    mutation,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        first_bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    result, _ = reviewer_result(
        first_bound,
        family="gemini",
    )

    inner = result.stable_mapping()
    mutate_outer = None

    if mutation == "outer-missing-field":
        mutate_outer = lambda outer: outer.pop(
            "review_result_id"
        )
    elif mutation == "outer-unknown-field":
        mutate_outer = lambda outer: outer.update({
            "unexpected": "field",
        })
    elif mutation == "outer-wrong-primitive":
        mutate_outer = lambda outer: outer.update({
            "review_result_id": [],
        })
    elif mutation == "inner-missing-field":
        inner.pop(
            "claimed_verdict"
        )
    elif mutation == "inner-unknown-field":
        inner["unexpected"] = "field"
    elif mutation == "inner-wrong-primitive":
        inner["invocation_id"] = []
    else:
        finding = {
            "scope": "WORKFLOW",
            "severity": "HIGH",
            "file": None,
            "evidence": {
                "nested": "not-text",
            },
            "recommended_fix": "reject malformed history",
        }

        inner["normalized_findings"] = [
            finding
        ]
        inner["findings_digest"] = canonical_digest(
            [finding]
        )

    appended = append_historical_result_mapping(
        store=store,
        authority=authority,
        result=result,
        inner=inner,
        mutate_outer=mutate_outer,
    )

    decision = owner_gate_decision(
        bound=next_review_campaign(
            first_bound
        ),
        store=store,
        authority=appended.authority,
        trusted_authority=appended.authority,
        results=(),
        history=history,
        registry=registry,
        policy=policy,
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )
    assert decision.counted_result_digests == ()
    assert decision.protected_action_authorized is False


def test_owner_gate_does_not_hide_unrelated_programmer_type_error(
    tmp_path,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    _, authority = authoritative_ledger(
        tmp_path
    )

    def broken_load_authoritative(_authority):
        raise TypeError(
            "unrelated programmer defect"
        )

    broken_store = SimpleNamespace(
        load_authoritative=(
            broken_load_authoritative
        )
    )

    with pytest.raises(
        TypeError,
        match="unrelated programmer defect",
    ):
        owner_gate_decision(
            bound=bound,
            store=broken_store,
            authority=authority,
            trusted_authority=authority,
            results=(),
            history=history,
            registry=registry,
            policy=policy,
        )


def test_false_continuity_signal_cannot_be_overwritten_by_vote_evidence(
    tmp_path,
    monkeypatch,
):
    (
        _,
        _,
        history,
        registry,
        policy,
        bound,
    ) = bound_fixture()

    store, authority = authoritative_ledger(
        tmp_path
    )

    recorded, results = record_ready_quorum(
        store=store,
        authority=authority,
        bound=bound,
    )

    monkeypatch.setattr(
        ReviewMeshLedgerV1,
        "verify_continuity",
        lambda _ledger: False,
    )

    decision = owner_gate_decision(
        bound=bound,
        store=store,
        authority=recorded.authority,
        trusted_authority=recorded.authority,
        results=results,
        history=history,
        registry=registry,
        policy=policy,
    )

    assert (
        decision.state
        is ReviewMeshDecisionState
        .BLOCKED_LEDGER_RECONCILIATION
    )
    assert decision.protected_action_authorized is False
