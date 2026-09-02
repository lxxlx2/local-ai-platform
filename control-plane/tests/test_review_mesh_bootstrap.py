from dataclasses import replace

import pytest

from local_ai_control.services.review_mesh_bootstrap import (
    BootstrapAuthorizationV1,
    BootstrapCompletePayloadV1,
    BootstrapExecutionVerdictV1,
    BootstrapGuardError,
    BootstrapMaterialPinsV1,
    BootstrapObservedIdentityV1,
    BootstrapQualificationExecutionV1,
    BootstrapSeedAuthorizationV1,
    BootstrapSeedProposalV1,
    BootstrapStateV1,
    BootstrapV1,
    HarnessInspectionRecordV1,
)
from local_ai_control.services.review_mesh_bootstrap_store import (
    BootstrapJournalStoreV1,
)
from local_ai_control.services.review_mesh_ledger import (
    LedgerRecordType,
    LedgerReconciliationError,
    ReviewMeshLedgerV1,
)
from local_ai_control.services.review_mesh_ledger_store import (
    ReviewMeshLedgerStoreV1,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
E = "e" * 64
F = "f" * 64
G = "0" * 64
H = "1" * 64
I = "2" * 64
J = "3" * 64
K = "4" * 64
L = "5" * 64
M = "6" * 64
N = "7" * 64

HEAD = "f2d5b7b0152b29da56e33e79583addf13c1ba634"


def authorization(**changes):
    values = dict(
        epoch_id="g0b-bootstrap-2026-09-02-v1",
        expires_at="2026-09-09T00:00:00+00:00",
        repository_id="lxxlx2/local-ai-platform",
        repository_sha=HEAD,
        protocol_revision="REVIEW_MESH_PROTOCOL_V1",
        protocol_digest=A,
        harness_version="reviewer-qualification-harness-v1",
        harness_implementation_digest=B,
        configuration_digest=C,
        allowed_provider_principals=("google-gemini", "mistral-ai"),
        allowed_adapter_principals=("adapter:gemini", "adapter:mistral"),
        disallowed_contributor_identity_digests=(N,),
        owner_record_digest=D,
        authorized_at="2026-09-02T00:00:00+00:00",
        zero_unapproved_paid_usage=True,
        read_only_qualification_scope=True,
        no_merge_deploy_runtime_authority=True,
    )
    values.update(changes)
    return BootstrapAuthorizationV1(**values)


def material_pins(auth, **changes):
    values = dict(
        epoch_id=auth.epoch_id,
        authorization_digest=auth.authorization_digest,
        public_fixture_manifest_digest=E,
        sealed_fixture_manifest_digest=F,
        sealed_label_manifest_digest=G,
        custody_version="g0b-custody-v1",
        custody_manifest_digest=H,
        variant_revision="metamorphic-v1",
        variant_generator_digest=I,
        scoring_revision="strong-review-scoring-v1",
        scoring_configuration_digest=J,
        owner_private_material_reference="object:sha256:sealed-material",
        owner_private_label_reference="object:sha256:sealed-labels",
        pinned_at="2026-09-02T01:00:00+00:00",
        disclosure_integrity_ok=True,
    )
    values.update(changes)
    return BootstrapMaterialPinsV1(**values)


def identity(*, family="gemini", **changes):
    if family == "gemini":
        values = dict(
            identity_record_digest=K,
            authenticated_adapter_principal="adapter:gemini",
            authentication_method="authenticated-session-v1",
            provider_principal="google-gemini",
            provider_account_scope="owner-free-tier-project",
            serving_backend="google-ai-studio",
            requested_model_id="gemini-3.6-flash",
            actual_model_id="gemini-3.6-flash",
            exact_actual_identity_observed=True,
            fallback_state="NO_FALLBACK",
            foundation_model="gemini-3.6-flash",
            foundation_revision="gemini-3.6-flash",
            foundation_lineage_class="google-gemini3",
            provider_receipt_digest=L,
            billing_tier="FREE",
            payg_enabled=False,
            privacy_permitted=True,
            privacy_decision_digest=M,
        )
    else:
        values = dict(
            identity_record_digest=I,
            authenticated_adapter_principal="adapter:mistral",
            authentication_method="authenticated-session-v1",
            provider_principal="mistral-ai",
            provider_account_scope="owner-free-studio",
            serving_backend="mistral-studio",
            requested_model_id="mistral-medium-2508",
            actual_model_id="mistral-medium-2508",
            exact_actual_identity_observed=True,
            fallback_state="NO_FALLBACK",
            foundation_model="mistral-medium",
            foundation_revision="2508",
            foundation_lineage_class="mistral-medium",
            provider_receipt_digest=H,
            billing_tier="FREE",
            payg_enabled=False,
            privacy_permitted=True,
            privacy_decision_digest=M,
        )
    values.update(changes)
    return BootstrapObservedIdentityV1(**values)


def inspection(auth, reviewer, *, ordinal):
    return HarnessInspectionRecordV1(
        inspection_id=f"inspection-{ordinal}",
        identity=reviewer,
        epoch_id=auth.epoch_id,
        harness_implementation_digest=auth.harness_implementation_digest,
        configuration_digest=auth.configuration_digest,
        inspected_input_digest=A if ordinal == 1 else B,
        provider_execution_receipt_digest=reviewer.provider_receipt_digest,
        inspected_at=f"2026-09-02T0{ordinal + 1}:00:00+00:00",
        passed=True,
        was_harness_contributor=False,
        was_registry_proposer=False,
    )


def execution(auth, pins, reviewer, *, ordinal, **changes):
    values = dict(
        attempt_id=f"qualification-{ordinal}",
        identity=reviewer,
        epoch_id=auth.epoch_id,
        harness_implementation_digest=auth.harness_implementation_digest,
        configuration_digest=auth.configuration_digest,
        public_fixture_manifest_digest=pins.public_fixture_manifest_digest,
        sealed_fixture_manifest_digest=pins.sealed_fixture_manifest_digest,
        nonce=("1" if ordinal == 1 else "2") * 32,
        input_digest=A if ordinal == 1 else B,
        provider_execution_receipt_digest=reviewer.provider_receipt_digest,
        qualification_evidence_digest=E if ordinal == 1 else F,
        completed_at=f"2026-09-02T0{ordinal + 3}:00:00+00:00",
        verdict=BootstrapExecutionVerdictV1.PASS,
        mandatory_fixtures_complete=True,
        mandatory_hidden_blocking_false_passes=0,
        label_leakage_detected=False,
        identity_ambiguous=False,
        unexpected_fallback=False,
    )
    values.update(changes)
    return BootstrapQualificationExecutionV1(**values)


def executions_complete():
    auth = authorization()
    pins = material_pins(auth)
    google = identity(family="gemini")
    mistral = identity(family="mistral")
    state = BootstrapV1().authorize(auth).pin_material(pins)
    state = state.inspect_harness((
        inspection(auth, google, ordinal=1),
        inspection(auth, mistral, ordinal=2),
    ))
    return state.complete_executions((
        execution(auth, pins, google, ordinal=1),
        execution(auth, pins, mistral, ordinal=2),
    ))


def seed_proposed():
    state = executions_complete()
    package = state.expected_bootstrap_package_digest(
        lineage_registry_snapshot_digest=G,
        qualification_registry_snapshot_digest=H,
    )
    proposal = BootstrapSeedProposalV1(
        epoch_id=state.authorization.epoch_id,
        lineage_registry_snapshot_digest=G,
        qualification_registry_snapshot_digest=H,
        qualification_evidence_digests=(E, F),
        bootstrap_package_digest=package,
        proposed_at="2026-09-02T07:00:00+00:00",
        all_proposed_strong_entries_zero_hidden_blocking_false_pass=True,
    )
    return state.propose_seed(proposal)


def completed_bootstrap():
    state = seed_proposed()
    proposal = state.seed_proposal
    seed_auth = BootstrapSeedAuthorizationV1(
        epoch_id=proposal.epoch_id,
        bootstrap_package_digest=proposal.bootstrap_package_digest,
        lineage_registry_snapshot_digest=proposal.lineage_registry_snapshot_digest,
        qualification_registry_snapshot_digest=(
            proposal.qualification_registry_snapshot_digest
        ),
        owner_record_digest=N,
        authorized_at="2026-09-02T08:00:00+00:00",
    )
    return state.complete(
        seed_auth,
        completed_at="2026-09-02T08:01:00+00:00",
    )


def test_exact_happy_path_requires_two_owner_records_and_protects_actions():
    completed = completed_bootstrap()
    assert completed.state is BootstrapStateV1.COMPLETE
    assert completed.complete_payload is not None
    assert completed.complete_payload.normal_mesh_policy_activated is True
    assert completed.complete_payload.protected_action_authorized is False
    assert len(completed.events) == 6


@pytest.mark.parametrize(
    "journal",
    (
        BootstrapV1(),
        BootstrapV1().authorize(authorization()),
        executions_complete(),
        seed_proposed(),
        completed_bootstrap(),
    ),
)
def test_bootstrap_journal_strict_round_trip_and_digest(journal):
    restored = BootstrapV1.from_mapping(journal.to_mapping())
    assert restored == journal
    assert restored.journal_digest == journal.journal_digest

    malformed = journal.to_mapping()
    malformed["unknown"] = True
    with pytest.raises(BootstrapGuardError, match="schema"):
        BootstrapV1.from_mapping(malformed)


def test_bootstrap_journal_store_is_cas_append_only_and_fail_closed(tmp_path):
    store = BootstrapJournalStoreV1(tmp_path / "bootstrap.json")
    empty = store.initialize()
    authorized = empty.authorize(authorization())
    persisted = store.advance(
        expected_journal_digest=empty.journal_digest,
        next_journal=authorized,
    )
    assert persisted == authorized
    assert store.load(expected_journal_digest=authorized.journal_digest) == authorized

    with pytest.raises(BootstrapGuardError, match="checkpoint"):
        store.load(expected_journal_digest=A)
    with pytest.raises(BootstrapGuardError, match="checkpoint"):
        store.advance(
            expected_journal_digest=empty.journal_digest,
            next_journal=authorized.pin_material(material_pins(authorization())),
        )


def test_bootstrap_journal_store_refuses_rewrite_and_terminal_advance(tmp_path):
    store = BootstrapJournalStoreV1(tmp_path / "bootstrap.json")
    empty = store.initialize()
    authorized = store.advance(
        expected_journal_digest=empty.journal_digest,
        next_journal=empty.authorize(authorization()),
    )
    aborted = authorized.abort(
        reason_code="OWNER_ABORT",
        evidence_digest=A,
        aborted_at="2026-09-02T00:00:01+00:00",
    )
    persisted = store.advance(
        expected_journal_digest=authorized.journal_digest,
        next_journal=aborted,
    )
    with pytest.raises(BootstrapGuardError, match="terminal"):
        store.advance(
            expected_journal_digest=persisted.journal_digest,
            next_journal=persisted,
        )


def test_bootstrap_complete_cannot_be_persisted_before_ledger_genesis(tmp_path):
    store = BootstrapJournalStoreV1(tmp_path / "bootstrap.json")
    # Persist each pre-completion transition exactly once.
    current = store.initialize()
    target = seed_proposed()
    for sequence in range(1, len(target.events) + 1):
        partial = BootstrapV1.from_mapping(
            BootstrapV1(
                events=target.events[:sequence],
                authorization=target.authorization,
                material_pins=(target.material_pins if sequence >= 2 else None),
                inspections=(target.inspections if sequence >= 3 else ()),
                executions=(target.executions if sequence >= 4 else ()),
                seed_proposal=(target.seed_proposal if sequence >= 5 else None),
            ).to_mapping()
        )
        current = store.advance(
            expected_journal_digest=current.journal_digest,
            next_journal=partial,
        )

    with pytest.raises(BootstrapGuardError, match="ledger genesis"):
        store.advance(
            expected_journal_digest=current.journal_digest,
            next_journal=completed_bootstrap(),
        )


def test_current_owner_work_authorization_cannot_use_dynamic_or_missing_bindings():
    with pytest.raises(BootstrapGuardError, match="harness implementation digest"):
        authorization(harness_implementation_digest="CURRENT")
    with pytest.raises(BootstrapGuardError, match="non-empty array"):
        authorization(allowed_adapter_principals=())


def test_authorization_cannot_enable_paid_or_protected_authority():
    with pytest.raises(BootstrapGuardError, match="forbidden authority"):
        authorization(zero_unapproved_paid_usage=False)
    with pytest.raises(BootstrapGuardError, match="forbidden authority"):
        authorization(no_merge_deploy_runtime_authority=False)


def test_authorization_must_have_bounded_forward_expiry():
    with pytest.raises(BootstrapGuardError, match="bounded forward"):
        authorization(expires_at="2026-09-02T00:00:00+00:00")


def test_material_pin_after_expiry_is_rejected():
    auth = authorization()
    state = BootstrapV1().authorize(auth)
    pins = material_pins(auth, pinned_at="2026-09-10T00:00:00+00:00")
    failed = state.pin_material(pins)
    assert failed.state is BootstrapStateV1.ABORTED
    assert failed.events[-1].reason_code == "GUARD_MISMATCH_PIN_MATERIAL"


def test_sealed_material_and_labels_must_be_separate():
    auth = authorization()
    with pytest.raises(BootstrapGuardError, match="separate"):
        material_pins(
            auth,
            owner_private_material_reference="object:sha256:same",
            owner_private_label_reference="object:sha256:same",
        )


def test_latest_alias_and_unexpected_fallback_fail_closed():
    with pytest.raises(BootstrapGuardError, match="alias-only"):
        identity(
            family="mistral",
            requested_model_id="mistral-medium-latest",
            actual_model_id="mistral-medium-latest",
        )
    with pytest.raises(BootstrapGuardError, match="fallback"):
        identity(fallback_state="FALLBACK")


def test_payg_and_privacy_denial_fail_before_bootstrap_execution():
    with pytest.raises(BootstrapGuardError, match="PAYG"):
        identity(payg_enabled=True)
    with pytest.raises(BootstrapGuardError, match="privacy"):
        identity(privacy_permitted=False)


def test_harness_inspection_requires_distinct_provider_and_foundation():
    auth = authorization()
    pins = material_pins(auth)
    state = BootstrapV1().authorize(auth).pin_material(pins)
    first = identity()
    second = identity(
        identity_record_digest=J,
        provider_receipt_digest=A,
    )
    failed = state.inspect_harness((
        inspection(auth, first, ordinal=1),
        inspection(auth, second, ordinal=2),
    ))
    assert failed.state is BootstrapStateV1.ABORTED
    assert failed.events[-1].reason_code == "GUARD_MISMATCH_INSPECT_HARNESS"


def test_harness_contributor_cannot_inspect_own_harness():
    auth = authorization()
    pins = material_pins(auth)
    state = BootstrapV1().authorize(auth).pin_material(pins)
    google = identity()
    mistral = identity(family="mistral")
    with pytest.raises(BootstrapGuardError, match="self-review"):
        replace(
            inspection(auth, google, ordinal=1),
            was_harness_contributor=True,
        )


def test_disallowed_contributor_identity_cannot_self_qualify():
    auth = authorization(disallowed_contributor_identity_digests=(K,))
    pins = material_pins(auth)
    state = BootstrapV1().authorize(auth).pin_material(pins)
    google = identity()
    mistral = identity(family="mistral")
    failed = state.inspect_harness((
        inspection(auth, google, ordinal=1),
        inspection(auth, mistral, ordinal=2),
    ))
    assert failed.state is BootstrapStateV1.ABORTED


@pytest.mark.parametrize(
    "change,match",
    [
        ({"verdict": BootstrapExecutionVerdictV1.FAIL}, "guard failed"),
        ({"mandatory_fixtures_complete": False}, "guard failed"),
        ({"mandatory_hidden_blocking_false_passes": 1}, "guard failed"),
        ({"label_leakage_detected": True}, "guard failed"),
        ({"identity_ambiguous": True}, "guard failed"),
        ({"unexpected_fallback": True}, "guard failed"),
    ],
)
def test_any_execution_guard_failure_blocks_epoch_progress(change, match):
    auth = authorization()
    pins = material_pins(auth)
    google = identity()
    mistral = identity(family="mistral")
    state = BootstrapV1().authorize(auth).pin_material(pins)
    state = state.inspect_harness((
        inspection(auth, google, ordinal=1),
        inspection(auth, mistral, ordinal=2),
    ))
    bad = execution(auth, pins, google, ordinal=1, **change)
    good = execution(auth, pins, mistral, ordinal=2)
    failed = state.complete_executions((bad, good))
    assert failed.state is BootstrapStateV1.ABORTED
    assert failed.events[-1].reason_code == "GUARD_MISMATCH_COMPLETE_EXECUTIONS"
    with pytest.raises(BootstrapGuardError, match="terminal"):
        failed.complete_executions((good, bad))


def test_seed_package_digest_binds_registry_and_every_execution():
    state = executions_complete()
    first = state.expected_bootstrap_package_digest(
        lineage_registry_snapshot_digest=G,
        qualification_registry_snapshot_digest=H,
    )
    second = state.expected_bootstrap_package_digest(
        lineage_registry_snapshot_digest=I,
        qualification_registry_snapshot_digest=H,
    )
    assert first != second


def test_seed_proposal_cannot_omit_failed_or_successful_attempt_evidence():
    state = executions_complete()
    package = state.expected_bootstrap_package_digest(
        lineage_registry_snapshot_digest=G,
        qualification_registry_snapshot_digest=H,
    )
    proposal = BootstrapSeedProposalV1(
        epoch_id=state.authorization.epoch_id,
        lineage_registry_snapshot_digest=G,
        qualification_registry_snapshot_digest=H,
        qualification_evidence_digests=(E,),
        bootstrap_package_digest=package,
        proposed_at="2026-09-02T07:00:00+00:00",
        all_proposed_strong_entries_zero_hidden_blocking_false_pass=True,
    )
    failed = state.propose_seed(proposal)
    assert failed.state is BootstrapStateV1.ABORTED
    assert failed.events[-1].reason_code == "GUARD_MISMATCH_PROPOSE_SEED"


def test_completion_requires_owner_record_bound_to_exact_seed_digests():
    state = seed_proposed()
    proposal = state.seed_proposal
    bad = BootstrapSeedAuthorizationV1(
        epoch_id=proposal.epoch_id,
        bootstrap_package_digest=A,
        lineage_registry_snapshot_digest=proposal.lineage_registry_snapshot_digest,
        qualification_registry_snapshot_digest=(
            proposal.qualification_registry_snapshot_digest
        ),
        owner_record_digest=N,
        authorized_at="2026-09-02T08:00:00+00:00",
    )
    failed = state.complete(bad, completed_at="2026-09-02T08:01:00+00:00")
    assert failed.state is BootstrapStateV1.ABORTED
    assert failed.events[-1].reason_code == "GUARD_MISMATCH_COMPLETE"


def test_abort_is_append_only_terminal():
    aborted = BootstrapV1().abort(
        reason_code="OWNER_ABORT",
        evidence_digest=A,
        aborted_at="2026-09-02T00:00:00+00:00",
    )
    assert aborted.state is BootstrapStateV1.ABORTED
    with pytest.raises(BootstrapGuardError, match="transition"):
        aborted.authorize(authorization())


def test_guard_failure_journals_abort_and_requires_fresh_epoch_retry():
    auth = authorization()
    active = BootstrapV1().authorize(auth)
    failed = active.pin_material(material_pins(auth, epoch_id="wrong-epoch"))

    assert failed.state is BootstrapStateV1.ABORTED
    assert active.state is BootstrapStateV1.OWNER_AUTHORIZED
    with pytest.raises(BootstrapGuardError, match="terminal"):
        failed.pin_material(material_pins(auth))
    with pytest.raises(BootstrapGuardError, match="new epoch"):
        failed.retry_with_new_epoch(auth)

    fresh_authorization = authorization(
        epoch_id="g0b-bootstrap-2026-09-03-v2",
        authorized_at="2026-09-03T00:00:00+00:00",
        expires_at="2026-09-10T00:00:00+00:00",
        owner_record_digest=A,
    )
    retry = failed.retry_with_new_epoch(fresh_authorization)
    assert retry.state is BootstrapStateV1.OWNER_AUTHORIZED
    assert retry.authorization.epoch_id != auth.epoch_id
    assert failed.state is BootstrapStateV1.ABORTED


def test_untyped_transition_input_is_journaled_as_terminal_abort():
    failed = BootstrapV1().authorize({"owner": "self-claimed"})
    assert failed.state is BootstrapStateV1.ABORTED
    assert failed.events[-1].reason_code == "GUARD_MISMATCH_AUTHORIZE"


@pytest.mark.parametrize(
    "field",
    ("nonce", "provider_execution_receipt_digest", "qualification_evidence_digest"),
)
def test_bootstrap_execution_replay_fields_abort_epoch(field):
    auth = authorization()
    pins = material_pins(auth)
    google = identity()
    mistral = identity(family="mistral")
    inspected = BootstrapV1().authorize(auth).pin_material(pins).inspect_harness((
        inspection(auth, google, ordinal=1),
        inspection(auth, mistral, ordinal=2),
    ))
    first = execution(auth, pins, google, ordinal=1)
    second = execution(auth, pins, mistral, ordinal=2)
    if field == "provider_execution_receipt_digest":
        second = replace(
            second,
            provider_execution_receipt_digest=first.provider_execution_receipt_digest,
            identity=replace(
                second.identity,
                provider_receipt_digest=first.provider_execution_receipt_digest,
            ),
        )
    else:
        second = replace(second, **{field: getattr(first, field)})
    failed = inspected.complete_executions((first, second))
    assert failed.state is BootstrapStateV1.ABORTED


def test_complete_snapshot_requires_seed_authorization_and_exact_cross_binding():
    completed = completed_bootstrap()
    with pytest.raises(BootstrapGuardError, match="payloads are inconsistent"):
        replace(completed, seed_authorization=None)

    mismatched_seed_authorization = replace(
        completed.seed_authorization,
        owner_record_digest=A,
    )
    with pytest.raises(BootstrapGuardError, match="completion payload binding"):
        replace(completed, seed_authorization=mismatched_seed_authorization)

    mismatched_payload = replace(
        completed.complete_payload,
        lineage_registry_snapshot_digest=A,
    )
    with pytest.raises(BootstrapGuardError, match="completion payload binding"):
        replace(completed, complete_payload=mismatched_payload)


def test_complete_event_and_payload_completion_times_are_identical():
    completed = completed_bootstrap()
    changed_payload = replace(
        completed.complete_payload,
        completed_at="2026-09-02T08:02:00+00:00",
    )
    with pytest.raises(BootstrapGuardError, match="completion event binding"):
        replace(completed, complete_payload=changed_payload)


def test_event_timestamps_are_strictly_monotonic_even_if_chain_is_otherwise_valid():
    auth = authorization()
    pinned = BootstrapV1().authorize(auth).pin_material(material_pins(auth))
    nonmonotonic = replace(
        pinned.events[-1],
        recorded_at=auth.authorized_at,
    )
    with pytest.raises(BootstrapGuardError, match="strictly monotonic"):
        replace(pinned, events=(pinned.events[0], nonmonotonic))


def test_every_authorized_transition_is_bounded_by_epoch_expiry():
    inspection_auth = authorization(expires_at="2026-09-02T02:30:00+00:00")
    inspection_pins = material_pins(inspection_auth)
    inspection_google = identity()
    inspection_mistral = identity(family="mistral")
    expired_inspection = (
        BootstrapV1()
        .authorize(inspection_auth)
        .pin_material(inspection_pins)
        .inspect_harness((
            inspection(inspection_auth, inspection_google, ordinal=1),
            inspection(inspection_auth, inspection_mistral, ordinal=2),
        ))
    )
    assert expired_inspection.state is BootstrapStateV1.ABORTED

    auth = authorization(expires_at="2026-09-02T04:30:00+00:00")
    pins = material_pins(auth)
    google = identity()
    mistral = identity(family="mistral")
    inspected = BootstrapV1().authorize(auth).pin_material(pins).inspect_harness((
        inspection(auth, google, ordinal=1),
        inspection(auth, mistral, ordinal=2),
    ))
    assert inspected.state is BootstrapStateV1.HARNESS_INSPECTED

    expired_executions = inspected.complete_executions((
        execution(auth, pins, google, ordinal=1),
        execution(auth, pins, mistral, ordinal=2),
    ))
    assert expired_executions.state is BootstrapStateV1.ABORTED

    proposal_state = executions_complete()
    package = proposal_state.expected_bootstrap_package_digest(
        lineage_registry_snapshot_digest=G,
        qualification_registry_snapshot_digest=H,
    )
    expired_proposal = BootstrapSeedProposalV1(
        epoch_id=proposal_state.authorization.epoch_id,
        lineage_registry_snapshot_digest=G,
        qualification_registry_snapshot_digest=H,
        qualification_evidence_digests=(E, F),
        bootstrap_package_digest=package,
        proposed_at="2026-09-10T00:00:00+00:00",
        all_proposed_strong_entries_zero_hidden_blocking_false_pass=True,
    )
    assert proposal_state.propose_seed(expired_proposal).state is BootstrapStateV1.ABORTED

    proposed = seed_proposed()
    seed_auth = replace(
        BootstrapSeedAuthorizationV1(
            epoch_id=proposed.seed_proposal.epoch_id,
            bootstrap_package_digest=proposed.seed_proposal.bootstrap_package_digest,
            lineage_registry_snapshot_digest=(
                proposed.seed_proposal.lineage_registry_snapshot_digest
            ),
            qualification_registry_snapshot_digest=(
                proposed.seed_proposal.qualification_registry_snapshot_digest
            ),
            owner_record_digest=N,
            authorized_at="2026-09-10T00:00:00+00:00",
        ),
    )
    assert proposed.complete(
        seed_auth,
        completed_at="2026-09-10T00:01:00+00:00",
    ).state is BootstrapStateV1.ABORTED


def test_invalid_completion_transition_time_aborts_with_valid_utc_journal_time():
    proposed = seed_proposed()
    proposal = proposed.seed_proposal
    seed_auth = BootstrapSeedAuthorizationV1(
        epoch_id=proposal.epoch_id,
        bootstrap_package_digest=proposal.bootstrap_package_digest,
        lineage_registry_snapshot_digest=proposal.lineage_registry_snapshot_digest,
        qualification_registry_snapshot_digest=(
            proposal.qualification_registry_snapshot_digest
        ),
        owner_record_digest=N,
        authorized_at="2026-09-02T08:00:00+00:00",
    )
    failed = proposed.complete(seed_auth, completed_at="not-a-timestamp")
    assert failed.state is BootstrapStateV1.ABORTED
    assert failed.events[-1].recorded_at.endswith("+00:00")


def test_bootstrap_complete_payload_strictly_rejects_unknown_and_authority_true():
    state = seed_proposed()
    proposal = state.seed_proposal
    raw = {
        "schema_version": "BOOTSTRAP_COMPLETE_PAYLOAD_V1",
        "protocol_version": "REVIEW_MESH_PROTOCOL_V1",
        "epoch_id": proposal.epoch_id,
        "bootstrap_package_digest": proposal.bootstrap_package_digest,
        "lineage_registry_snapshot_digest": proposal.lineage_registry_snapshot_digest,
        "qualification_registry_snapshot_digest": (
            proposal.qualification_registry_snapshot_digest
        ),
        "owner_seed_authorization_digest": N,
        "completed_at": "2026-09-02T08:01:00+00:00",
        "normal_mesh_policy_activated": True,
        "protected_action_authorized": False,
    }
    payload = BootstrapCompletePayloadV1.from_mapping(raw)
    assert payload.protected_action_authorized is False
    with pytest.raises(BootstrapGuardError, match="schema"):
        BootstrapCompletePayloadV1.from_mapping({**raw, "unexpected": True})
    with pytest.raises(BootstrapGuardError, match="protected"):
        BootstrapCompletePayloadV1.from_mapping({
            **raw,
            "protected_action_authorized": True,
        })


def test_bootstrap_complete_is_unique_first_ledger_record_and_not_tombstonable():
    payload = completed_bootstrap().complete_payload
    ledger = ReviewMeshLedgerV1()

    with pytest.raises(LedgerReconciliationError, match="use append_bootstrap"):
        ledger.append(
            record_type=LedgerRecordType.BOOTSTRAP_COMPLETE,
            payload=payload.stable_mapping(),
            related_task_id="g0b-bootstrap",
            related_request_id=None,
            related_campaign_id=None,
            actor_provenance_digest=A,
            ingestion_receipt_digest=B,
            idempotency_key="bootstrap-complete-1",
            created_at=payload.completed_at,
        )

    anchored = ledger.append_bootstrap_complete(
        payload=payload.stable_mapping(),
        related_task_id="g0b-bootstrap",
        actor_provenance_digest=A,
        ingestion_receipt_digest=B,
        idempotency_key="bootstrap-complete-1",
        created_at=payload.completed_at,
    )

    assert anchored.record.sequence_number == 1
    assert anchored.ledger.require_bootstrap_complete(
        expected_payload_digest=payload.payload_digest,
    ) == anchored.record

    normal = anchored.ledger.append(
        record_type=LedgerRecordType.REVIEW_REQUEST,
        payload={"diagnostic": True},
        related_task_id="g0b-bootstrap",
        related_request_id="rr1:" + A,
        related_campaign_id="rc1:" + B,
        actor_provenance_digest=A,
        ingestion_receipt_digest=B,
        idempotency_key="normal-1",
        created_at="2026-09-02T08:02:00+00:00",
    )

    with pytest.raises(LedgerReconciliationError, match="cannot be tombstoned"):
        normal.ledger.append(
            record_type=LedgerRecordType.TOMBSTONE,
            payload={"reason": "forbidden"},
            related_task_id="g0b-bootstrap",
            related_request_id=None,
            related_campaign_id=None,
            actor_provenance_digest=A,
            ingestion_receipt_digest=B,
            idempotency_key="tombstone-bootstrap",
            created_at="2026-09-02T08:03:00+00:00",
            superseded_or_revoked_record_digest=anchored.record.record_digest,
        )


def test_durable_bootstrap_initialization_requires_empty_authoritative_store(tmp_path):
    bootstrap = completed_bootstrap()
    payload = bootstrap.complete_payload
    store = ReviewMeshLedgerStoreV1(tmp_path / "ledger.json")
    authority = store.initialize_authority(authority_id="owner:g0b-bootstrap")

    initialized = store.initialize_bootstrap_complete_authoritatively(
        authority=authority,
        bootstrap=bootstrap,
        related_task_id="g0b-bootstrap",
        actor_provenance_digest=A,
        ingestion_receipt_digest=B,
        idempotency_key="bootstrap-complete-1",
        created_at=payload.completed_at,
    )

    assert initialized.authority.trusted_record_count == 1
    assert initialized.snapshot.record_count == 1
    assert initialized.snapshot.ledger.require_bootstrap_complete(
        expected_payload_digest=payload.payload_digest,
    ) == initialized.record

    with pytest.raises(LedgerReconciliationError, match="empty durable ledger"):
        store.initialize_bootstrap_complete_authoritatively(
            authority=initialized.authority,
            bootstrap=bootstrap,
            related_task_id="g0b-bootstrap",
            actor_provenance_digest=A,
            ingestion_receipt_digest=B,
            idempotency_key="bootstrap-complete-2",
            created_at=payload.completed_at,
        )

    with pytest.raises(LedgerReconciliationError, match="authoritative append"):
        store.append(
            record_type=LedgerRecordType.REVIEW_REQUEST,
            payload={"diagnostic": True},
            related_task_id="g0b-bootstrap",
            related_request_id="rr1:" + A,
            related_campaign_id="rc1:" + B,
            actor_provenance_digest=A,
            ingestion_receipt_digest=B,
            idempotency_key="non-authoritative-after-bootstrap",
            created_at="2026-09-02T08:02:00+00:00",
        )


def test_durable_bootstrap_initialization_rejects_standalone_typed_payload(tmp_path):
    payload = completed_bootstrap().complete_payload
    store = ReviewMeshLedgerStoreV1(tmp_path / "ledger.json")
    authority = store.initialize_authority(authority_id="owner:g0b-bootstrap")

    with pytest.raises(LedgerReconciliationError, match="complete typed journal"):
        store.initialize_bootstrap_complete_authoritatively(
            authority=authority,
            bootstrap=payload,
            related_task_id="g0b-bootstrap",
            actor_provenance_digest=A,
            ingestion_receipt_digest=B,
            idempotency_key="standalone-payload",
            created_at=payload.completed_at,
        )


def test_legacy_diagnostic_ledger_cannot_claim_bootstrap_activation():
    diagnostic = ReviewMeshLedgerV1().append(
        record_type=LedgerRecordType.REVIEW_RESULT,
        payload={"diagnostic": True},
        related_task_id="pre-bootstrap",
        related_request_id="rr1:" + A,
        related_campaign_id="rc1:" + B,
        actor_provenance_digest=A,
        ingestion_receipt_digest=B,
        idempotency_key="diagnostic-1",
        created_at="2026-09-02T00:00:00+00:00",
    ).ledger

    with pytest.raises(LedgerReconciliationError, match="anchor is missing"):
        diagnostic.require_bootstrap_complete(expected_payload_digest=C)

    with pytest.raises(LedgerReconciliationError, match="empty ledger"):
        diagnostic.append_bootstrap_complete(
            payload=completed_bootstrap().complete_payload.stable_mapping(),
            related_task_id="g0b-bootstrap",
            actor_provenance_digest=A,
            ingestion_receipt_digest=B,
            idempotency_key="bootstrap-after-diagnostic",
            created_at="2026-09-02T08:01:00+00:00",
        )


# ---------------------------------------------------------------------------
# G0-B durable BOOTSTRAP_V1 adversarial persistence matrix
# ---------------------------------------------------------------------------

def _write_bootstrap_store_bytes(store, payload: bytes) -> None:
    """Test-only hostile durable-state replacement."""
    import os

    with open(store.path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(store.path, 0o600)


def _canonical_bootstrap_bytes(journal: BootstrapV1) -> bytes:
    from local_ai_control.services.review_mesh_protocol import (
        canonical_json_bytes,
    )

    return canonical_json_bytes(journal.to_mapping())


def _persist_authorized_and_pinned(store):
    empty = store.initialize()

    auth = authorization()

    authorized = store.advance(
        expected_journal_digest=empty.journal_digest,
        next_journal=empty.authorize(auth),
    )

    pinned = store.advance(
        expected_journal_digest=authorized.journal_digest,
        next_journal=authorized.pin_material(material_pins(auth)),
    )

    return empty, authorized, pinned


def test_bootstrap_journal_restart_loads_exact_trusted_checkpoint(tmp_path):
    path = tmp_path / "bootstrap.json"

    first_store = BootstrapJournalStoreV1(path)
    _, _, pinned = _persist_authorized_and_pinned(first_store)

    trusted_checkpoint = pinned.journal_digest

    restarted_store = BootstrapJournalStoreV1(path)

    restored = restarted_store.load(
        expected_journal_digest=trusted_checkpoint,
    )

    assert restored == pinned
    assert restored.journal_digest == trusted_checkpoint


def test_bootstrap_journal_rejects_valid_rollback_after_restart(tmp_path):
    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    empty = store.initialize()
    auth = authorization()

    authorized = store.advance(
        expected_journal_digest=empty.journal_digest,
        next_journal=empty.authorize(auth),
    )
    authorized_bytes = path.read_bytes()

    pinned = store.advance(
        expected_journal_digest=authorized.journal_digest,
        next_journal=authorized.pin_material(material_pins(auth)),
    )

    trusted_checkpoint = pinned.journal_digest

    # Hostile replacement with an earlier but completely valid journal.
    _write_bootstrap_store_bytes(store, authorized_bytes)

    restarted = BootstrapJournalStoreV1(path)

    with pytest.raises(BootstrapGuardError, match="checkpoint"):
        restarted.load(
            expected_journal_digest=trusted_checkpoint,
        )


def test_bootstrap_journal_rejects_same_length_valid_fork(tmp_path):
    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    empty = store.initialize()

    auth_a = authorization(
        owner_record_digest="8" * 64,
    )
    auth_b = authorization(
        owner_record_digest="9" * 64,
    )

    branch_a = empty.authorize(auth_a)
    branch_b = empty.authorize(auth_b)

    assert len(branch_a.events) == len(branch_b.events)
    assert branch_a.journal_digest != branch_b.journal_digest

    persisted = store.advance(
        expected_journal_digest=empty.journal_digest,
        next_journal=branch_a,
    )

    trusted_checkpoint = persisted.journal_digest

    # Replace durable state with a structurally valid competing branch.
    _write_bootstrap_store_bytes(
        store,
        _canonical_bootstrap_bytes(branch_b),
    )

    restarted = BootstrapJournalStoreV1(path)

    with pytest.raises(BootstrapGuardError, match="checkpoint"):
        restarted.load(
            expected_journal_digest=trusted_checkpoint,
        )


def test_bootstrap_journal_rejects_fresh_genesis_replacement(tmp_path):
    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    _, _, pinned = _persist_authorized_and_pinned(store)
    trusted_checkpoint = pinned.journal_digest

    fresh_genesis = BootstrapV1()

    assert fresh_genesis.journal_digest != trusted_checkpoint

    _write_bootstrap_store_bytes(
        store,
        _canonical_bootstrap_bytes(fresh_genesis),
    )

    restarted = BootstrapJournalStoreV1(path)

    with pytest.raises(BootstrapGuardError, match="checkpoint"):
        restarted.load(
            expected_journal_digest=trusted_checkpoint,
        )


def test_bootstrap_journal_rejects_truncated_durable_state(tmp_path):
    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    _, _, pinned = _persist_authorized_and_pinned(store)
    trusted_checkpoint = pinned.journal_digest

    payload = path.read_bytes()

    assert len(payload) > 16

    _write_bootstrap_store_bytes(
        store,
        payload[: len(payload) // 2],
    )

    restarted = BootstrapJournalStoreV1(path)

    with pytest.raises(
        BootstrapGuardError,
        match="JSON|schema|journal|invalid",
    ):
        restarted.load(
            expected_journal_digest=trusted_checkpoint,
        )


def test_bootstrap_journal_rejects_tampered_valid_json(tmp_path):
    import json

    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    _, _, pinned = _persist_authorized_and_pinned(store)
    trusted_checkpoint = pinned.journal_digest

    raw = json.loads(path.read_text())

    # Keep syntactically valid JSON while mutating retained durable content.
    raw["state"] = "UNINITIALIZED"

    from local_ai_control.services.review_mesh_protocol import (
        canonical_json_bytes,
    )

    _write_bootstrap_store_bytes(
        store,
        canonical_json_bytes(raw),
    )

    restarted = BootstrapJournalStoreV1(path)

    with pytest.raises(BootstrapGuardError):
        restarted.load(
            expected_journal_digest=trusted_checkpoint,
        )


def test_bootstrap_journal_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    empty = store.initialize()
    trusted_checkpoint = empty.journal_digest

    payload = path.read_bytes()

    assert payload.startswith(b"{")

    hostile = (
        b'{"schema_version":"BOOTSTRAP_JOURNAL_V1",'
        + payload[1:]
    )

    _write_bootstrap_store_bytes(store, hostile)

    restarted = BootstrapJournalStoreV1(path)

    with pytest.raises(
        BootstrapGuardError,
        match="duplicate|JSON",
    ):
        restarted.load(
            expected_journal_digest=trusted_checkpoint,
        )


def test_bootstrap_journal_rejects_noncanonical_json(tmp_path):
    import json

    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    empty = store.initialize()
    trusted_checkpoint = empty.journal_digest

    decoded = json.loads(path.read_text())

    # Semantically identical JSON with formatting/order representation
    # different from canonical_json_bytes().
    hostile = json.dumps(
        decoded,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")

    assert hostile != path.read_bytes()

    _write_bootstrap_store_bytes(store, hostile)

    restarted = BootstrapJournalStoreV1(path)

    with pytest.raises(
        BootstrapGuardError,
        match="canonical",
    ):
        restarted.load(
            expected_journal_digest=trusted_checkpoint,
        )


def test_bootstrap_journal_rejects_symlink_replacement(tmp_path):
    import os

    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    _, _, pinned = _persist_authorized_and_pinned(store)
    trusted_checkpoint = pinned.journal_digest

    attacker = tmp_path / "attacker-bootstrap.json"
    attacker.write_bytes(
        _canonical_bootstrap_bytes(BootstrapV1())
    )
    os.chmod(attacker, 0o600)

    path.unlink()
    path.symlink_to(attacker)

    restarted = BootstrapJournalStoreV1

    # Constructor itself may fail closed because canonical path changes.
    with pytest.raises(
        (BootstrapGuardError, ValueError, OSError),
    ):
        candidate = restarted(path)
        candidate.load(
            expected_journal_digest=trusted_checkpoint,
        )


def test_bootstrap_journal_rejects_illegal_multi_event_append(tmp_path):
    store = BootstrapJournalStoreV1(
        tmp_path / "bootstrap.json"
    )

    empty = store.initialize()

    target = (
        BootstrapV1()
        .authorize(authorization())
        .pin_material(material_pins(authorization()))
    )

    assert len(target.events) == 2

    with pytest.raises(
        BootstrapGuardError,
        match="one-event append",
    ):
        store.advance(
            expected_journal_digest=empty.journal_digest,
            next_journal=target,
        )


def test_bootstrap_journal_rejects_retained_payload_rewrite(tmp_path):
    store = BootstrapJournalStoreV1(
        tmp_path / "bootstrap.json"
    )

    empty = store.initialize()
    auth = authorization()

    authorized = store.advance(
        expected_journal_digest=empty.journal_digest,
        next_journal=empty.authorize(auth),
    )

    legitimate_next = authorized.pin_material(
        material_pins(auth)
    )

    # BootstrapV1 itself rejects inconsistent event/payload bindings.
    # To exercise the store boundary independently, simulate a hostile
    # in-memory mutation after construction.
    hostile_auth = authorization(
        owner_record_digest="8" * 64,
    )

    hostile = legitimate_next
    object.__setattr__(
        hostile,
        "authorization",
        hostile_auth,
    )

    with pytest.raises(
        BootstrapGuardError,
        match="rewrites retained payload",
    ):
        store.advance(
            expected_journal_digest=authorized.journal_digest,
            next_journal=hostile,
        )


# ---------------------------------------------------------------------------
# G0-B durable BOOTSTRAP_V1 crash-boundary matrix
# ---------------------------------------------------------------------------

def test_bootstrap_store_replace_failure_preserves_previous_checkpoint(
    tmp_path,
    monkeypatch,
):
    import os

    path = tmp_path / "bootstrap.json"
    store = BootstrapJournalStoreV1(path)

    empty = store.initialize()
    auth = authorization()

    authorized = empty.authorize(auth)

    original_replace = os.replace

    def fail_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failure"):
        store.advance(
            expected_journal_digest=empty.journal_digest,
            next_journal=authorized,
        )

    monkeypatch.setattr(os, "replace", original_replace)

    restarted = BootstrapJournalStoreV1(path)

    restored = restarted.load(
        expected_journal_digest=empty.journal_digest,
    )

    assert restored == empty


def test_bootstrap_store_post_replace_fsync_failure_fails_closed(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "bootstrap.json"
    store = BootstrapJournalStoreV1(path)

    empty = store.initialize()
    auth = authorization()
    authorized = empty.authorize(auth)

    original_fsync_directory = store._fsync_directory

    calls = {"count": 0}

    def fail_directory_fsync(target):
        calls["count"] += 1
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(
        store,
        "_fsync_directory",
        fail_directory_fsync,
    )

    with pytest.raises(
        OSError,
        match="directory fsync failure",
    ):
        store.advance(
            expected_journal_digest=empty.journal_digest,
            next_journal=authorized,
        )

    assert calls["count"] >= 1

    monkeypatch.setattr(
        store,
        "_fsync_directory",
        original_fsync_directory,
    )

    restarted = BootstrapJournalStoreV1(path)

    # os.replace may already have published the new journal.
    # Because the call failed, the caller must still hold the old
    # trusted checkpoint. Any published-new state therefore fails
    # closed against that old checkpoint.
    try:
        restored = restarted.load(
            expected_journal_digest=empty.journal_digest,
        )
    except BootstrapGuardError as exc:
        assert "checkpoint" in str(exc)
    else:
        assert restored == empty


def test_bootstrap_store_ignores_unpublished_temporary_file(
    tmp_path,
):
    import os

    path = tmp_path / "bootstrap.json"
    store = BootstrapJournalStoreV1(path)

    empty = store.initialize()

    pending = tmp_path / ".bootstrap.json.hostile.tmp"

    pending.write_bytes(
        _canonical_bootstrap_bytes(
            BootstrapV1().authorize(authorization())
        )
    )
    os.chmod(pending, 0o600)

    restarted = BootstrapJournalStoreV1(path)

    restored = restarted.load(
        expected_journal_digest=empty.journal_digest,
    )

    assert restored == empty
    assert pending.exists()


def test_bootstrap_store_restart_after_successful_advance_uses_new_checkpoint(
    tmp_path,
):
    path = tmp_path / "bootstrap.json"

    store = BootstrapJournalStoreV1(path)

    empty = store.initialize()
    auth = authorization()

    authorized = store.advance(
        expected_journal_digest=empty.journal_digest,
        next_journal=empty.authorize(auth),
    )

    restarted = BootstrapJournalStoreV1(path)

    restored = restarted.load(
        expected_journal_digest=authorized.journal_digest,
    )

    assert restored == authorized

    with pytest.raises(
        BootstrapGuardError,
        match="checkpoint",
    ):
        restarted.load(
            expected_journal_digest=empty.journal_digest,
        )
