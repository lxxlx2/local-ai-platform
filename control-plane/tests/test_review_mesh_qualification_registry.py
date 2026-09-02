from dataclasses import replace
from datetime import datetime, timezone

import pytest

from local_ai_control.services.review_mesh_bootstrap import BootstrapCompletePayloadV1
from local_ai_control.services.review_mesh_decisions import (
    LineageApprovalState,
)
from local_ai_control.services.review_mesh_protocol import (
    ExecutionLocality,
    PROTOCOL_VERSION,
    ReviewerClass,
    RiskLevel,
)
from local_ai_control.services.review_mesh_qualification_registry import (
    FallbackStateV1,
    GENESIS_SNAPSHOT_DIGEST,
    IdentityPrecisionV1,
    LineageIdentityEntryV1,
    LineageRegistryV1,
    PermittedFallbackIdentityV1,
    QualificationEntryV1,
    QualificationRegistryV1,
    QualificationStatusV1,
    RegistryActivationStateV1,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
E = "e" * 64
F = "f" * 64

CREATED = "2026-09-01T00:00:00+00:00"
REVIEWED = "2026-09-01T01:00:00+00:00"
ACTIVATED = "2026-09-01T02:00:00+00:00"
AS_OF = "2026-09-02T00:00:00+00:00"
EXPIRES = "2026-10-01T00:00:00+00:00"


def lineage_entry(**changes) -> LineageIdentityEntryV1:
    values = dict(
        reviewer_registry_id="reviewer:google:gemini-3.6-flash-001",
        authenticated_adapter_principal="adapter:gemini-review",
        allowed_authentication_methods=("api-key-v1", "oauth-v1"),
        provider_principal="google-gemini",
        provider_account_scope="project-owner-free",
        serving_backend="google-gemini-api",
        endpoint_class="generate-content",
        requested_model_aliases=("gemini-3.6-flash-001",),
        actual_model_id="gemini-3.6-flash-001",
        identity_precision=IdentityPrecisionV1.EXACT,
        permitted_fallback_identities=(),
        foundation_model="gemini-3.6-flash",
        foundation_revision="gemini-3.6-flash-001",
        foundation_lineage_class="google-gemini3",
        correlation_group="google-provider",
        hosted_copy_relationship="NONE",
        derivative_relationship="NONE",
        execution_locality=ExecutionLocality.REMOTE,
        data_egress_permitted=True,
        actual_egress_destination="google-gemini-api",
        eligible_reviewer_classes=(ReviewerClass.STRONG_P1,),
        eligible_risk_levels=(RiskLevel.P1,),
        lineage_evidence_digest=A,
        approval_state=LineageApprovalState.APPROVED,
        activation_state=RegistryActivationStateV1.ACTIVE,
        requalification_conditions=("model-change", "policy-change"),
        independent_review_record_digest=B,
        created_at=CREATED,
        reviewed_at=REVIEWED,
        activated_at=ACTIVATED,
        expires_at=EXPIRES,
        activation_record_digest=C,
        ledger_sequence=11,
    )
    values.update(changes)
    return LineageIdentityEntryV1(**values)


def lineage_registry(*entries, **changes) -> LineageRegistryV1:
    if not entries:
        entries = (lineage_entry(),)
    values = dict(
        sequence_number=1,
        previous_snapshot_digest=GENESIS_SNAPSHOT_DIGEST,
        policy_revision="lineage-policy-v1",
        entries=tuple(entries),
        activation_state=RegistryActivationStateV1.ACTIVE,
        independent_review_record_digest=D,
        created_at=CREATED,
        reviewed_at=REVIEWED,
        activated_at=ACTIVATED,
        expires_at=EXPIRES,
        activation_record_digest=E,
        ledger_sequence=12,
    )
    values.update(changes)
    return LineageRegistryV1(**values)


def qualification_entry(
    lineage: LineageRegistryV1,
    **changes,
) -> QualificationEntryV1:
    identity = lineage.entries[0]
    values = dict(
        qualification_registry_id="qualification:google:gemini-3.6-flash-001",
        reviewer_registry_id=identity.reviewer_registry_id,
        requested_reviewer_registry_id=identity.reviewer_registry_id,
        authenticated_adapter_principal=identity.authenticated_adapter_principal,
        authentication_method="api-key-v1",
        provider_principal=identity.provider_principal,
        provider_account_scope=identity.provider_account_scope,
        serving_backend=identity.serving_backend,
        endpoint_class=identity.endpoint_class,
        requested_model_id=identity.actual_model_id,
        actual_model_id=identity.actual_model_id,
        identity_precision=IdentityPrecisionV1.EXACT,
        fallback_state=FallbackStateV1.NO_FALLBACK,
        foundation_model=identity.foundation_model,
        foundation_revision=identity.foundation_revision,
        foundation_lineage_class=identity.foundation_lineage_class,
        execution_locality=identity.execution_locality,
        data_egress_permitted=identity.data_egress_permitted,
        actual_egress_destination=identity.actual_egress_destination,
        identity_envelope_digest=A,
        protocol_revision=PROTOCOL_VERSION,
        benchmark_harness_policy_revision="review-bench-v1",
        benchmark_version="benchmark-v1",
        custody_version="custody-v1",
        harness_revision="harness-v1",
        harness_digest=B,
        scoring_revision="scoring-v1",
        scoring_digest=C,
        public_fixture_manifest_digest=D,
        sealed_fixture_manifest_digest=E,
        sealed_label_manifest_digest=F,
        lineage_registry_snapshot_digest=lineage.snapshot_digest,
        qualification_evidence_digest=A,
        privacy_mode="RESTRICTED",
        egress_decision_digest=B,
        status=QualificationStatusV1.QUALIFIED_STRONG_P1,
        qualified_reviewer_class=ReviewerClass.STRONG_P1,
        eligible_risk_levels=(RiskLevel.P1,),
        activation_state=RegistryActivationStateV1.ACTIVE,
        requalification_conditions=("model-change", "harness-change"),
        independent_review_record_digest=C,
        created_at=CREATED,
        reviewed_at=REVIEWED,
        activated_at=ACTIVATED,
        expires_at=EXPIRES,
        activation_record_digest=D,
        ledger_sequence=13,
    )
    values.update(changes)
    return QualificationEntryV1(**values)


def qualification_registry(
    lineage: LineageRegistryV1,
    *entries,
    **changes,
) -> QualificationRegistryV1:
    if not entries:
        entries = (qualification_entry(lineage),)
    values = dict(
        sequence_number=1,
        previous_snapshot_digest=GENESIS_SNAPSHOT_DIGEST,
        policy_revision="qualification-policy-v1",
        entries=tuple(entries),
        activation_state=RegistryActivationStateV1.ACTIVE,
        independent_review_record_digest=E,
        created_at=CREATED,
        reviewed_at=REVIEWED,
        activated_at=ACTIVATED,
        expires_at=EXPIRES,
        activation_record_digest=F,
        ledger_sequence=14,
    )
    values.update(changes)
    return QualificationRegistryV1(**values)


def compile_eligibility(
    qualifications: QualificationRegistryV1,
    lineage: LineageRegistryV1,
    qualification_registry_id: str | None = None,
    **changes,
):
    values = dict(
        qualification_registry_id=(
            qualification_registry_id
            or qualifications.entries[0].qualification_registry_id
        ),
        lineage_registry=lineage,
        current_snapshot_digest=qualifications.snapshot_digest,
        current_lineage_snapshot_digest=lineage.snapshot_digest,
        expected_protocol_revision=PROTOCOL_VERSION,
        expected_benchmark_harness_policy_revision="review-bench-v1",
        as_of=AS_OF,
    )
    values.update(changes)
    return qualifications.compile_g0a_eligibility(**values)


def bootstrap_seed_registries():
    seed_lineage_entry = replace(
        lineage_entry(),
        activation_state=RegistryActivationStateV1.PROPOSED,
        activated_at=None,
        activation_record_digest=None,
        ledger_sequence=None,
    )
    lineage = lineage_registry(
        seed_lineage_entry,
        activation_state=RegistryActivationStateV1.PROPOSED,
        activated_at=None,
        activation_record_digest=None,
        ledger_sequence=None,
    )
    seed_qualification_entry = replace(
        qualification_entry(lineage),
        activation_state=RegistryActivationStateV1.PROPOSED,
        activated_at=None,
        activation_record_digest=None,
        ledger_sequence=None,
    )
    qualifications = qualification_registry(
        lineage,
        seed_qualification_entry,
        activation_state=RegistryActivationStateV1.PROPOSED,
        activated_at=None,
        activation_record_digest=None,
        ledger_sequence=None,
    )
    completion = BootstrapCompletePayloadV1(
        epoch_id="g0b-bootstrap-v1",
        bootstrap_package_digest=A,
        lineage_registry_snapshot_digest=lineage.snapshot_digest,
        qualification_registry_snapshot_digest=qualifications.snapshot_digest,
        owner_seed_authorization_digest=B,
        completed_at="2026-09-01T03:00:00+00:00",
    )
    return lineage, qualifications, completion


def test_lineage_entry_digest_is_stable_and_roundtrips_strictly():
    first = lineage_entry(
        allowed_authentication_methods=("oauth-v1", "api-key-v1"),
        requalification_conditions=("policy-change", "model-change"),
    )
    second = lineage_entry()

    assert first.entry_digest == second.entry_digest
    assert LineageIdentityEntryV1.from_mapping(first.to_mapping()) == first


def test_qualification_entry_digest_is_stable_and_roundtrips_strictly():
    lineage = lineage_registry()
    first = qualification_entry(
        lineage,
        requalification_conditions=("harness-change", "model-change"),
    )
    second = qualification_entry(lineage)

    assert first.entry_digest == second.entry_digest
    assert QualificationEntryV1.from_mapping(first.to_mapping()) == first


def test_registry_digest_is_order_independent_and_roundtrips_strictly():
    google = lineage_entry()
    mistral = lineage_entry(
        reviewer_registry_id="reviewer:mistral:large-2411",
        authenticated_adapter_principal="adapter:mistral-review",
        provider_principal="mistral",
        provider_account_scope="workspace-owner-free",
        serving_backend="mistral-api",
        endpoint_class="chat-completions",
        requested_model_aliases=("mistral-large-2411",),
        actual_model_id="mistral-large-2411",
        foundation_model="mistral-large",
        foundation_revision="mistral-large-2411",
        foundation_lineage_class="mistral-large",
        correlation_group="mistral-provider",
        actual_egress_destination="mistral-api",
    )
    first = lineage_registry(google, mistral)
    second = lineage_registry(mistral, google)

    assert first.snapshot_digest == second.snapshot_digest
    assert LineageRegistryV1.from_mapping(first.to_mapping()) == first

    qualifications = qualification_registry(first)
    assert QualificationRegistryV1.from_mapping(
        qualifications.to_mapping()
    ) == qualifications


@pytest.mark.parametrize(
    "factory,parser",
    [
        (lambda: lineage_entry().to_mapping(), LineageIdentityEntryV1.from_mapping),
        (lambda: lineage_registry().to_mapping(), LineageRegistryV1.from_mapping),
        (
            lambda: qualification_entry(lineage_registry()).to_mapping(),
            QualificationEntryV1.from_mapping,
        ),
        (
            lambda: qualification_registry(lineage_registry()).to_mapping(),
            QualificationRegistryV1.from_mapping,
        ),
    ],
)
def test_serialized_objects_reject_unknown_fields(factory, parser):
    mapping = factory()
    mapping["unknown"] = True

    with pytest.raises(ValueError, match="fields are invalid"):
        parser(mapping)


@pytest.mark.parametrize(
    "factory,parser,digest_field",
    [
        (lambda: lineage_entry().to_mapping(), LineageIdentityEntryV1.from_mapping, "entry_digest"),
        (lambda: lineage_registry().to_mapping(), LineageRegistryV1.from_mapping, "snapshot_digest"),
        (
            lambda: qualification_entry(lineage_registry()).to_mapping(),
            QualificationEntryV1.from_mapping,
            "entry_digest",
        ),
        (
            lambda: qualification_registry(lineage_registry()).to_mapping(),
            QualificationRegistryV1.from_mapping,
            "snapshot_digest",
        ),
    ],
)
def test_serialized_objects_reject_digest_tampering(factory, parser, digest_field):
    mapping = factory()
    mapping[digest_field] = "0" * 64

    with pytest.raises(ValueError, match="digest mismatch"):
        parser(mapping)


@pytest.mark.parametrize(
    "precision",
    [
        IdentityPrecisionV1.ALIAS,
        IdentityPrecisionV1.AMBIGUOUS,
        IdentityPrecisionV1.LATEST,
    ],
)
def test_active_lineage_rejects_every_non_exact_identity_precision(precision):
    with pytest.raises(ValueError, match="exact identity"):
        lineage_entry(identity_precision=precision)


@pytest.mark.parametrize(
    "actual_model_id",
    [
        "gemini-alias",
        "gemini.ambiguous.3",
        "gemini/latest",
        "latest",
        "gemini-preview",
        "gemini-auto",
        "gemini-default",
        "gemini-unknown",
        "gemini-unpinned",
    ],
)
def test_active_lineage_rejects_every_ambiguous_actual_model_token(actual_model_id):
    with pytest.raises(ValueError, match="actual model is an alias"):
        lineage_entry(
            actual_model_id=actual_model_id,
            requested_model_aliases=(actual_model_id,),
        )


@pytest.mark.parametrize(
    "precision",
    [
        IdentityPrecisionV1.ALIAS,
        IdentityPrecisionV1.AMBIGUOUS,
        IdentityPrecisionV1.LATEST,
    ],
)
def test_active_qualification_rejects_every_non_exact_identity_precision(precision):
    lineage = lineage_registry()
    with pytest.raises(ValueError, match="identity must be exact"):
        qualification_entry(lineage, identity_precision=precision)


def test_permitted_fallback_ids_must_also_be_exact():
    with pytest.raises(ValueError, match="fallback actual model id is ambiguous or unpinned"):
        lineage_entry(
            permitted_fallback_identities=(
                PermittedFallbackIdentityV1(
                    authenticated_adapter_principal="adapter:mistral-review",
                    provider_principal="mistral",
                    provider_account_scope="workspace-owner-free",
                    serving_backend="mistral-api",
                    endpoint_class="chat-completions",
                    actual_model_id="mistral-latest",
                ),
            ),
        )


def test_active_entry_requires_durable_activation_binding():
    with pytest.raises(ValueError, match="activation binding"):
        lineage_entry(activation_record_digest=None)
    with pytest.raises(ValueError, match="activation binding"):
        qualification_entry(lineage_registry(), ledger_sequence=None)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"reviewed_at": "2026-08-31T23:59:59+00:00"}, "review precedes creation"),
        ({"activated_at": "2026-09-01T00:30:00+00:00"}, "invalid activation interval"),
        ({"expires_at": ACTIVATED}, "invalid activation interval"),
    ],
)
def test_activation_time_order_is_fail_closed(changes, match):
    with pytest.raises(ValueError, match=match):
        lineage_entry(**changes)


def test_active_registries_must_not_be_empty():
    with pytest.raises(ValueError, match="active.*no entries|must not be empty"):
        lineage_registry(entries=())
    with pytest.raises(ValueError, match="active.*no entries|must not be empty"):
        qualification_registry(lineage_registry(), entries=())


@pytest.mark.parametrize(
    "registry_factory",
    [
        lambda: lineage_registry(entries=(object(),)),
        lambda: qualification_registry(lineage_registry(), entries=(object(),)),
    ],
)
def test_direct_registry_construction_rejects_malformed_entry_types(registry_factory):
    with pytest.raises(ValueError, match="entry type is invalid"):
        registry_factory()


def test_direct_entries_reject_bool_ledger_sequence_and_list_risk_envelope():
    with pytest.raises(ValueError, match="positive unsigned"):
        lineage_entry(ledger_sequence=True)
    with pytest.raises(ValueError, match="must be a non-empty tuple"):
        qualification_entry(
            lineage_registry(),
            eligible_risk_levels=[RiskLevel.P1],
        )


def test_registry_successor_requires_exact_digest_sequence_and_monotonic_creation():
    first = lineage_registry()
    valid = lineage_registry(
        sequence_number=2,
        previous_snapshot_digest=first.snapshot_digest,
        created_at="2026-09-02T00:00:00+00:00",
        reviewed_at="2026-09-02T01:00:00+00:00",
        activated_at="2026-09-02T02:00:00+00:00",
    )
    valid.validate_successor(first)

    with pytest.raises(ValueError, match="predecessor digest mismatch"):
        replace(valid, previous_snapshot_digest=F).validate_successor(first)
    with pytest.raises(ValueError, match="sequence gap"):
        replace(valid, sequence_number=3).validate_successor(first)


def test_compile_requires_current_active_registry_and_entry():
    lineage = lineage_registry()
    qualifications = qualification_registry(lineage)
    eligibility = compile_eligibility(qualifications, lineage)

    assert eligibility.active is True
    assert eligibility.qualification_registry_snapshot_digest == qualifications.snapshot_digest
    assert eligibility.qualification_evidence_digest == qualifications.entries[0].qualification_evidence_digest

    with pytest.raises(ValueError, match="qualification registry is stale"):
        compile_eligibility(qualifications, lineage, current_snapshot_digest=F)
    with pytest.raises(ValueError, match="qualification registry is not current"):
        compile_eligibility(qualifications, lineage, as_of=EXPIRES)


def test_unknown_foundation_lineage_cannot_compile():
    lineage = lineage_registry()
    entry = qualification_entry(
        lineage,
        foundation_lineage_class="unregistered-foundation-lineage",
    )
    qualifications = qualification_registry(lineage, entry)

    with pytest.raises(ValueError, match="exact identity does not match"):
        compile_eligibility(qualifications, lineage)


def test_fallback_requires_complete_identity_and_blocks_cross_provider_substitution():
    model_only_cross_provider_permission = PermittedFallbackIdentityV1(
        authenticated_adapter_principal="adapter:mistral-review",
        provider_principal="google-gemini",
        provider_account_scope="workspace-owner-free",
        serving_backend="mistral-api",
        endpoint_class="chat-completions",
        actual_model_id="mistral-large-2411",
    )
    requested = lineage_entry(
        reviewer_registry_id="reviewer:google:gemini-pro-001",
        requested_model_aliases=("gemini-pro-001",),
        actual_model_id="gemini-pro-001",
        foundation_revision="gemini-pro-001",
        permitted_fallback_identities=(model_only_cross_provider_permission,),
    )
    actual = lineage_entry(
        reviewer_registry_id="reviewer:mistral:large-2411",
        authenticated_adapter_principal="adapter:mistral-review",
        allowed_authentication_methods=("api-key-v1",),
        provider_principal="mistral",
        provider_account_scope="workspace-owner-free",
        serving_backend="mistral-api",
        endpoint_class="chat-completions",
        requested_model_aliases=("mistral-large-2411",),
        actual_model_id="mistral-large-2411",
        foundation_model="mistral-large",
        foundation_revision="mistral-large-2411",
        foundation_lineage_class="mistral-large",
        correlation_group="mistral-provider",
        actual_egress_destination="mistral-api",
    )
    lineage = lineage_registry(requested, actual)
    fallback = qualification_entry(
        lineage,
        qualification_registry_id="qualification:mistral:fallback-from-google",
        reviewer_registry_id=actual.reviewer_registry_id,
        requested_reviewer_registry_id=requested.reviewer_registry_id,
        authenticated_adapter_principal=actual.authenticated_adapter_principal,
        provider_principal=actual.provider_principal,
        provider_account_scope=actual.provider_account_scope,
        serving_backend=actual.serving_backend,
        endpoint_class=actual.endpoint_class,
        requested_model_id=requested.actual_model_id,
        actual_model_id=actual.actual_model_id,
        fallback_state=FallbackStateV1.POLICY_PERMITTED_FALLBACK,
        foundation_model=actual.foundation_model,
        foundation_revision=actual.foundation_revision,
        foundation_lineage_class=actual.foundation_lineage_class,
        actual_egress_destination=actual.actual_egress_destination,
    )
    qualifications = qualification_registry(lineage, fallback)

    with pytest.raises(ValueError, match="fallback actual identity is not policy permitted"):
        compile_eligibility(qualifications, lineage)


def test_qualification_lineage_snapshot_binding_is_exact():
    lineage = lineage_registry()
    stale_entry = qualification_entry(lineage, lineage_registry_snapshot_digest=F)
    qualifications = qualification_registry(lineage, stale_entry)

    with pytest.raises(ValueError, match="lineage snapshot binding is stale"):
        compile_eligibility(qualifications, lineage)


def test_all_persisted_timestamps_and_as_of_must_be_utc():
    with pytest.raises(ValueError, match="UTC"):
        lineage_entry(created_at="2026-09-01T07:00:00+07:00")

    lineage = lineage_registry()
    qualifications = qualification_registry(lineage)
    with pytest.raises(ValueError, match="UTC"):
        compile_eligibility(
            qualifications,
            lineage,
            as_of="2026-09-02T07:00:00+07:00",
        )


def test_is_current_at_handles_utc_boundaries_exactly():
    entry = lineage_entry()
    activated = datetime.fromisoformat(ACTIVATED)
    expires = datetime.fromisoformat(EXPIRES)

    assert entry.is_current_at(activated)
    assert entry.is_current_at(expires) is False
    assert entry.is_current_at(datetime(2026, 9, 2, tzinfo=timezone.utc))


def test_compiled_g0a_snapshot_remains_bound_to_full_registry_digest():
    registry = lineage_registry()
    compiled = registry.compile_g0a_snapshot(
        current_snapshot_digest=registry.snapshot_digest,
        as_of=AS_OF,
    )

    assert compiled.binding_digest == registry.snapshot_digest
    assert compiled.source_registry_snapshot_digest == registry.snapshot_digest


def test_bootstrap_complete_activates_exact_proposed_seed_without_digest_cycle():
    lineage, qualifications, completion = bootstrap_seed_registries()

    with pytest.raises(ValueError, match="not active"):
        lineage.compile_g0a_snapshot(
            current_snapshot_digest=lineage.snapshot_digest,
            as_of=AS_OF,
        )
    with pytest.raises(ValueError, match="not active"):
        compile_eligibility(qualifications, lineage)

    compiled = lineage.compile_g0a_snapshot(
        current_snapshot_digest=lineage.snapshot_digest,
        as_of=AS_OF,
        bootstrap_complete_payload=completion,
    )
    eligibility = compile_eligibility(
        qualifications,
        lineage,
        bootstrap_complete_payload=completion,
    )

    assert compiled.binding_digest == lineage.snapshot_digest
    assert eligibility.active is True


def test_bootstrap_seed_activation_rejects_other_registry_digests():
    lineage, qualifications, completion = bootstrap_seed_registries()
    wrong = replace(completion, lineage_registry_snapshot_digest=F)

    with pytest.raises(ValueError, match="not active"):
        lineage.compile_g0a_snapshot(
            current_snapshot_digest=lineage.snapshot_digest,
            as_of=AS_OF,
            bootstrap_complete_payload=wrong,
        )

    wrong_qualification = replace(
        completion,
        qualification_registry_snapshot_digest=F,
    )
    with pytest.raises(ValueError, match="not active"):
        compile_eligibility(
            qualifications,
            lineage,
            bootstrap_complete_payload=wrong_qualification,
        )
