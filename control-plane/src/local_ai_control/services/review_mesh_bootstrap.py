"""Fail-closed one-time reviewer-registry bootstrap ceremony.

This module implements the trust-root state machine from Reviewer
Qualification Policy section 10.  It deliberately uses a qualification-time
identity record rather than ``IdentityEnvelopeV1``: the normal review identity
envelope already refers to qualification evidence and therefore cannot be used
to create the first qualification evidence without a digest cycle.

The state machine has no provider, network, merge, deployment, runtime, or
model-start capability.  Its inputs are trusted orchestrator observations and
Owner records.  Every transition is immutable and content addressed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import re
from typing import Mapping

from .review_mesh_protocol import (
    PROTOCOL_VERSION,
    canonical_digest,
)


BOOTSTRAP_SCHEMA_VERSION = "BOOTSTRAP_V1"
BOOTSTRAP_EVENT_SCHEMA_VERSION = "BOOTSTRAP_EVENT_V1"
BOOTSTRAP_COMPLETE_PAYLOAD_VERSION = "BOOTSTRAP_COMPLETE_PAYLOAD_V1"

BOOTSTRAP_EVENT_GENESIS_DIGEST = canonical_digest({
    "schema_version": BOOTSTRAP_EVENT_SCHEMA_VERSION,
    "genesis": "BOOTSTRAP_V1_EVENT_GENESIS",
})

_SHA40 = re.compile(r"[a-f0-9]{40}")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/+@-]{1,256}")
_AMBIGUOUS_IDENTITY = re.compile(
    r"(?:^|[-_.:/+@])"
    r"(latest|preview|auto|default|unknown|unpinned|alias|ambiguous)"
    r"(?:$|[-_.:/+@])",
    re.IGNORECASE,
)


class BootstrapGuardError(ValueError):
    """Raised when an object is malformed before it can enter the journal."""


_GUARD_ABORT_REASON_PREFIX = "GUARD_MISMATCH"


def _strict_keys(raw: object, required: set[str], label: str) -> Mapping:
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise BootstrapGuardError(f"{label} schema is invalid")
    return raw


def _text(value: object, label: str) -> str:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise BootstrapGuardError(f"{label} is invalid")
    return value


def _sha40(value: object, label: str) -> str:
    if type(value) is not str or not _SHA40.fullmatch(value):
        raise BootstrapGuardError(f"{label} must be a lowercase 40-hex SHA")
    return value


def _sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise BootstrapGuardError(f"{label} must be a lowercase SHA-256")
    return value


def _positive_u64(value: object, label: str) -> int:
    if type(value) is not int or value < 1 or value >= 2**64:
        raise BootstrapGuardError(f"{label} is invalid")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str or not value.endswith("+00:00"):
        raise BootstrapGuardError(f"{label} must be RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise BootstrapGuardError(f"{label} is invalid") from error
    if parsed.tzinfo != timezone.utc:
        raise BootstrapGuardError(f"{label} must use +00:00")
    return parsed


def _sorted_unique_identifiers(values: object, label: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or not values:
        raise BootstrapGuardError(f"{label} must be a non-empty array")
    normalized = tuple(_text(value, label) for value in values)
    if len(normalized) != len(set(normalized)):
        raise BootstrapGuardError(f"{label} contains duplicates")
    return tuple(sorted(normalized))


def _sorted_unique_digests(
    values: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or (not values and not allow_empty):
        raise BootstrapGuardError(f"{label} has invalid cardinality")
    normalized = tuple(_sha256(value, label) for value in values)
    if len(normalized) != len(set(normalized)):
        raise BootstrapGuardError(f"{label} contains duplicates")
    return tuple(sorted(normalized))


class BootstrapStateV1(str, Enum):
    UNINITIALIZED = "BOOTSTRAP_UNINITIALIZED"
    OWNER_AUTHORIZED = "BOOTSTRAP_OWNER_AUTHORIZED"
    MATERIAL_PINNED = "BOOTSTRAP_MATERIAL_PINNED"
    HARNESS_INSPECTED = "BOOTSTRAP_HARNESS_INSPECTED"
    EXECUTIONS_COMPLETE = "BOOTSTRAP_EXECUTIONS_COMPLETE"
    SEED_PROPOSED = "BOOTSTRAP_SEED_PROPOSED"
    COMPLETE = "BOOTSTRAP_COMPLETE"
    ABORTED = "BOOTSTRAP_ABORTED"


class BootstrapExecutionVerdictV1(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class BootstrapAuthorizationV1:
    epoch_id: str
    expires_at: str
    repository_id: str
    repository_sha: str
    protocol_revision: str
    protocol_digest: str
    harness_version: str
    harness_implementation_digest: str
    configuration_digest: str
    allowed_provider_principals: tuple[str, ...]
    allowed_adapter_principals: tuple[str, ...]
    disallowed_contributor_identity_digests: tuple[str, ...]
    owner_record_digest: str
    authorized_at: str
    zero_unapproved_paid_usage: bool
    read_only_qualification_scope: bool
    no_merge_deploy_runtime_authority: bool
    protocol_version: str = PROTOCOL_VERSION
    schema_version: str = BOOTSTRAP_SCHEMA_VERSION

    def __post_init__(self):
        for label, value in (
            ("bootstrap epoch", self.epoch_id),
            ("repository id", self.repository_id),
            ("protocol revision", self.protocol_revision),
            ("harness version", self.harness_version),
        ):
            _text(value, label)
        _sha40(self.repository_sha, "bootstrap repository SHA")
        for label, value in (
            ("protocol digest", self.protocol_digest),
            ("harness implementation digest", self.harness_implementation_digest),
            ("configuration digest", self.configuration_digest),
            ("Owner record digest", self.owner_record_digest),
        ):
            _sha256(value, label)
        object.__setattr__(
            self,
            "allowed_provider_principals",
            _sorted_unique_identifiers(
                self.allowed_provider_principals,
                "allowed provider principals",
            ),
        )
        object.__setattr__(
            self,
            "allowed_adapter_principals",
            _sorted_unique_identifiers(
                self.allowed_adapter_principals,
                "allowed adapter principals",
            ),
        )
        object.__setattr__(
            self,
            "disallowed_contributor_identity_digests",
            _sorted_unique_digests(
                self.disallowed_contributor_identity_digests,
                "disallowed contributor identities",
                allow_empty=True,
            ),
        )
        authorized = _timestamp(self.authorized_at, "authorization timestamp")
        expiry = _timestamp(self.expires_at, "authorization expiry")
        if expiry <= authorized:
            raise BootstrapGuardError("bootstrap authorization expiry is not bounded forward")
        if self.protocol_version != PROTOCOL_VERSION:
            raise BootstrapGuardError("bootstrap protocol version mismatch")
        if self.schema_version != BOOTSTRAP_SCHEMA_VERSION:
            raise BootstrapGuardError("bootstrap schema version mismatch")
        if not all((
            self.zero_unapproved_paid_usage is True,
            self.read_only_qualification_scope is True,
            self.no_merge_deploy_runtime_authority is True,
        )):
            raise BootstrapGuardError("bootstrap authorization expands forbidden authority")

    def stable_mapping(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "epoch_id": self.epoch_id,
            "expires_at": self.expires_at,
            "repository_id": self.repository_id,
            "repository_sha": self.repository_sha,
            "protocol_revision": self.protocol_revision,
            "protocol_digest": self.protocol_digest,
            "harness_version": self.harness_version,
            "harness_implementation_digest": self.harness_implementation_digest,
            "configuration_digest": self.configuration_digest,
            "allowed_provider_principals": list(self.allowed_provider_principals),
            "allowed_adapter_principals": list(self.allowed_adapter_principals),
            "disallowed_contributor_identity_digests": list(
                self.disallowed_contributor_identity_digests
            ),
            "owner_record_digest": self.owner_record_digest,
            "authorized_at": self.authorized_at,
            "zero_unapproved_paid_usage": self.zero_unapproved_paid_usage,
            "read_only_qualification_scope": self.read_only_qualification_scope,
            "no_merge_deploy_runtime_authority": (
                self.no_merge_deploy_runtime_authority
            ),
        }

    @property
    def authorization_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapAuthorizationV1":
        value = _strict_keys(raw, set(cls.__dataclass_fields__), "bootstrap authorization")
        return cls(
            **{
                **dict(value),
                "allowed_provider_principals": tuple(value["allowed_provider_principals"]),
                "allowed_adapter_principals": tuple(value["allowed_adapter_principals"]),
                "disallowed_contributor_identity_digests": tuple(
                    value["disallowed_contributor_identity_digests"]
                ),
            }
        )


@dataclass(frozen=True)
class BootstrapMaterialPinsV1:
    epoch_id: str
    authorization_digest: str
    public_fixture_manifest_digest: str
    sealed_fixture_manifest_digest: str
    sealed_label_manifest_digest: str
    custody_version: str
    custody_manifest_digest: str
    variant_revision: str
    variant_generator_digest: str
    scoring_revision: str
    scoring_configuration_digest: str
    owner_private_material_reference: str
    owner_private_label_reference: str
    pinned_at: str
    disclosure_integrity_ok: bool

    def __post_init__(self):
        for label, value in (
            ("bootstrap epoch", self.epoch_id),
            ("custody version", self.custody_version),
            ("variant revision", self.variant_revision),
            ("scoring revision", self.scoring_revision),
            ("private material reference", self.owner_private_material_reference),
            ("private label reference", self.owner_private_label_reference),
        ):
            _text(value, label)
        for label, value in (
            ("authorization digest", self.authorization_digest),
            ("public fixture manifest digest", self.public_fixture_manifest_digest),
            ("sealed fixture manifest digest", self.sealed_fixture_manifest_digest),
            ("sealed label manifest digest", self.sealed_label_manifest_digest),
            ("custody manifest digest", self.custody_manifest_digest),
            ("variant generator digest", self.variant_generator_digest),
            ("scoring configuration digest", self.scoring_configuration_digest),
        ):
            _sha256(value, label)
        _timestamp(self.pinned_at, "material pin timestamp")
        if self.disclosure_integrity_ok is not True:
            raise BootstrapGuardError("bootstrap material disclosure integrity failed")
        if self.owner_private_material_reference == self.owner_private_label_reference:
            raise BootstrapGuardError("sealed material and labels must use separate references")

    def stable_mapping(self) -> dict:
        return {
            "epoch_id": self.epoch_id,
            "authorization_digest": self.authorization_digest,
            "public_fixture_manifest_digest": self.public_fixture_manifest_digest,
            "sealed_fixture_manifest_digest": self.sealed_fixture_manifest_digest,
            "sealed_label_manifest_digest": self.sealed_label_manifest_digest,
            "custody_version": self.custody_version,
            "custody_manifest_digest": self.custody_manifest_digest,
            "variant_revision": self.variant_revision,
            "variant_generator_digest": self.variant_generator_digest,
            "scoring_revision": self.scoring_revision,
            "scoring_configuration_digest": self.scoring_configuration_digest,
            "owner_private_material_reference": self.owner_private_material_reference,
            "owner_private_label_reference": self.owner_private_label_reference,
            "pinned_at": self.pinned_at,
            "disclosure_integrity_ok": self.disclosure_integrity_ok,
        }

    @property
    def material_pins_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapMaterialPinsV1":
        value = _strict_keys(raw, set(cls.__dataclass_fields__), "bootstrap material pins")
        return cls(**dict(value))


@dataclass(frozen=True)
class BootstrapObservedIdentityV1:
    identity_record_digest: str
    authenticated_adapter_principal: str
    authentication_method: str
    provider_principal: str
    provider_account_scope: str
    serving_backend: str
    requested_model_id: str
    actual_model_id: str
    exact_actual_identity_observed: bool
    fallback_state: str
    foundation_model: str
    foundation_revision: str
    foundation_lineage_class: str
    provider_receipt_digest: str
    billing_tier: str
    payg_enabled: bool
    privacy_permitted: bool
    privacy_decision_digest: str

    def __post_init__(self):
        for label, value in (
            ("adapter principal", self.authenticated_adapter_principal),
            ("authentication method", self.authentication_method),
            ("provider principal", self.provider_principal),
            ("provider account scope", self.provider_account_scope),
            ("serving backend", self.serving_backend),
            ("requested model", self.requested_model_id),
            ("actual model", self.actual_model_id),
            ("foundation model", self.foundation_model),
            ("foundation revision", self.foundation_revision),
            ("foundation lineage", self.foundation_lineage_class),
            ("billing tier", self.billing_tier),
        ):
            _text(value, label)
        for label, value in (
            ("identity record digest", self.identity_record_digest),
            ("provider receipt digest", self.provider_receipt_digest),
            ("privacy decision digest", self.privacy_decision_digest),
        ):
            _sha256(value, label)
        if self.exact_actual_identity_observed is not True:
            raise BootstrapGuardError("bootstrap actual identity is not exact")
        if self.fallback_state != "NO_FALLBACK":
            raise BootstrapGuardError("bootstrap execution used fallback")
        if self.requested_model_id != self.actual_model_id:
            raise BootstrapGuardError("requested and actual bootstrap model differ")
        for label, value in (
            ("actual model identity", self.actual_model_id),
            ("foundation model identity", self.foundation_model),
            ("foundation revision identity", self.foundation_revision),
            ("foundation lineage identity", self.foundation_lineage_class),
        ):
            if _AMBIGUOUS_IDENTITY.search(value):
                raise BootstrapGuardError(f"{label} is alias-only or ambiguous")
        if self.payg_enabled is not False:
            raise BootstrapGuardError("PAYG must be disabled for bootstrap")
        if self.privacy_permitted is not True:
            raise BootstrapGuardError("bootstrap egress is not privacy permitted")

    @property
    def stable_mapping(self) -> dict:
        return {
            "identity_record_digest": self.identity_record_digest,
            "authenticated_adapter_principal": self.authenticated_adapter_principal,
            "authentication_method": self.authentication_method,
            "provider_principal": self.provider_principal,
            "provider_account_scope": self.provider_account_scope,
            "serving_backend": self.serving_backend,
            "requested_model_id": self.requested_model_id,
            "actual_model_id": self.actual_model_id,
            "exact_actual_identity_observed": self.exact_actual_identity_observed,
            "fallback_state": self.fallback_state,
            "foundation_model": self.foundation_model,
            "foundation_revision": self.foundation_revision,
            "foundation_lineage_class": self.foundation_lineage_class,
            "provider_receipt_digest": self.provider_receipt_digest,
            "billing_tier": self.billing_tier,
            "payg_enabled": self.payg_enabled,
            "privacy_permitted": self.privacy_permitted,
            "privacy_decision_digest": self.privacy_decision_digest,
        }

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapObservedIdentityV1":
        value = _strict_keys(raw, set(cls.__dataclass_fields__), "bootstrap observed identity")
        return cls(**dict(value))


@dataclass(frozen=True)
class HarnessInspectionRecordV1:
    inspection_id: str
    identity: BootstrapObservedIdentityV1
    epoch_id: str
    harness_implementation_digest: str
    configuration_digest: str
    inspected_input_digest: str
    provider_execution_receipt_digest: str
    inspected_at: str
    passed: bool
    was_harness_contributor: bool
    was_registry_proposer: bool

    def __post_init__(self):
        _text(self.inspection_id, "inspection id")
        _text(self.epoch_id, "inspection epoch")
        for label, value in (
            ("inspection harness digest", self.harness_implementation_digest),
            ("inspection config digest", self.configuration_digest),
            ("inspection input digest", self.inspected_input_digest),
            ("inspection provider receipt", self.provider_execution_receipt_digest),
        ):
            _sha256(value, label)
        _timestamp(self.inspected_at, "inspection timestamp")
        if self.provider_execution_receipt_digest != self.identity.provider_receipt_digest:
            raise BootstrapGuardError("inspection receipt/identity mismatch")
        if self.passed is not True:
            raise BootstrapGuardError("bootstrap harness inspection did not pass")
        if self.was_harness_contributor or self.was_registry_proposer:
            raise BootstrapGuardError("bootstrap inspection is self-review")

    def stable_mapping(self) -> dict:
        return {
            "inspection_id": self.inspection_id,
            "identity": self.identity.stable_mapping,
            "epoch_id": self.epoch_id,
            "harness_implementation_digest": self.harness_implementation_digest,
            "configuration_digest": self.configuration_digest,
            "inspected_input_digest": self.inspected_input_digest,
            "provider_execution_receipt_digest": self.provider_execution_receipt_digest,
            "inspected_at": self.inspected_at,
            "passed": self.passed,
            "was_harness_contributor": self.was_harness_contributor,
            "was_registry_proposer": self.was_registry_proposer,
        }

    @property
    def inspection_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    @classmethod
    def from_mapping(cls, raw: object) -> "HarnessInspectionRecordV1":
        value = _strict_keys(raw, set(cls.__dataclass_fields__), "bootstrap harness inspection")
        return cls(
            **{
                **dict(value),
                "identity": BootstrapObservedIdentityV1.from_mapping(value["identity"]),
            }
        )


@dataclass(frozen=True)
class BootstrapQualificationExecutionV1:
    attempt_id: str
    identity: BootstrapObservedIdentityV1
    epoch_id: str
    harness_implementation_digest: str
    configuration_digest: str
    public_fixture_manifest_digest: str
    sealed_fixture_manifest_digest: str
    nonce: str
    input_digest: str
    provider_execution_receipt_digest: str
    qualification_evidence_digest: str
    completed_at: str
    verdict: BootstrapExecutionVerdictV1
    mandatory_fixtures_complete: bool
    mandatory_hidden_blocking_false_passes: int
    label_leakage_detected: bool
    identity_ambiguous: bool
    unexpected_fallback: bool

    def __post_init__(self):
        _text(self.attempt_id, "qualification attempt id")
        _text(self.epoch_id, "qualification epoch")
        if type(self.nonce) is not str or not re.fullmatch(r"[a-f0-9]{32,128}", self.nonce):
            raise BootstrapGuardError("qualification nonce is invalid")
        for label, value in (
            ("execution harness digest", self.harness_implementation_digest),
            ("execution config digest", self.configuration_digest),
            ("execution public manifest digest", self.public_fixture_manifest_digest),
            ("execution sealed manifest digest", self.sealed_fixture_manifest_digest),
            ("execution input digest", self.input_digest),
            ("execution provider receipt", self.provider_execution_receipt_digest),
            ("qualification evidence digest", self.qualification_evidence_digest),
        ):
            _sha256(value, label)
        _timestamp(self.completed_at, "qualification completion timestamp")
        if not isinstance(self.verdict, BootstrapExecutionVerdictV1):
            raise BootstrapGuardError("qualification verdict is invalid")
        if self.provider_execution_receipt_digest != self.identity.provider_receipt_digest:
            raise BootstrapGuardError("qualification receipt/identity mismatch")
        if type(self.mandatory_hidden_blocking_false_passes) is not int:
            raise BootstrapGuardError("mandatory false PASS count is invalid")
        if self.mandatory_hidden_blocking_false_passes < 0:
            raise BootstrapGuardError("mandatory false PASS count is negative")
        for label, value in (
            ("mandatory fixtures complete", self.mandatory_fixtures_complete),
            ("label leakage detected", self.label_leakage_detected),
            ("identity ambiguous", self.identity_ambiguous),
            ("unexpected fallback", self.unexpected_fallback),
        ):
            if type(value) is not bool:
                raise BootstrapGuardError(f"{label} must be boolean")

    def stable_mapping(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "identity": self.identity.stable_mapping,
            "epoch_id": self.epoch_id,
            "harness_implementation_digest": self.harness_implementation_digest,
            "configuration_digest": self.configuration_digest,
            "public_fixture_manifest_digest": self.public_fixture_manifest_digest,
            "sealed_fixture_manifest_digest": self.sealed_fixture_manifest_digest,
            "nonce": self.nonce,
            "input_digest": self.input_digest,
            "provider_execution_receipt_digest": self.provider_execution_receipt_digest,
            "qualification_evidence_digest": self.qualification_evidence_digest,
            "completed_at": self.completed_at,
            "verdict": self.verdict.value,
            "mandatory_fixtures_complete": self.mandatory_fixtures_complete,
            "mandatory_hidden_blocking_false_passes": (
                self.mandatory_hidden_blocking_false_passes
            ),
            "label_leakage_detected": self.label_leakage_detected,
            "identity_ambiguous": self.identity_ambiguous,
            "unexpected_fallback": self.unexpected_fallback,
        }

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapQualificationExecutionV1":
        value = _strict_keys(
            raw,
            set(cls.__dataclass_fields__),
            "bootstrap qualification execution",
        )
        return cls(
            **{
                **dict(value),
                "identity": BootstrapObservedIdentityV1.from_mapping(value["identity"]),
                "verdict": BootstrapExecutionVerdictV1(value["verdict"]),
            }
        )


@dataclass(frozen=True)
class BootstrapSeedProposalV1:
    epoch_id: str
    lineage_registry_snapshot_digest: str
    qualification_registry_snapshot_digest: str
    qualification_evidence_digests: tuple[str, ...]
    bootstrap_package_digest: str
    proposed_at: str
    all_proposed_strong_entries_zero_hidden_blocking_false_pass: bool

    def __post_init__(self):
        _text(self.epoch_id, "seed epoch")
        for label, value in (
            ("lineage registry digest", self.lineage_registry_snapshot_digest),
            ("qualification registry digest", self.qualification_registry_snapshot_digest),
            ("bootstrap package digest", self.bootstrap_package_digest),
        ):
            _sha256(value, label)
        object.__setattr__(
            self,
            "qualification_evidence_digests",
            _sorted_unique_digests(
                self.qualification_evidence_digests,
                "qualification evidence digests",
            ),
        )
        _timestamp(self.proposed_at, "seed proposal timestamp")
        if self.all_proposed_strong_entries_zero_hidden_blocking_false_pass is not True:
            raise BootstrapGuardError("seed contains a mandatory hidden false PASS")

    def stable_mapping(self) -> dict:
        return {
            "epoch_id": self.epoch_id,
            "lineage_registry_snapshot_digest": self.lineage_registry_snapshot_digest,
            "qualification_registry_snapshot_digest": (
                self.qualification_registry_snapshot_digest
            ),
            "qualification_evidence_digests": list(self.qualification_evidence_digests),
            "bootstrap_package_digest": self.bootstrap_package_digest,
            "proposed_at": self.proposed_at,
            "all_proposed_strong_entries_zero_hidden_blocking_false_pass": (
                self.all_proposed_strong_entries_zero_hidden_blocking_false_pass
            ),
        }

    @property
    def proposal_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapSeedProposalV1":
        value = _strict_keys(raw, set(cls.__dataclass_fields__), "bootstrap seed proposal")
        return cls(
            **{
                **dict(value),
                "qualification_evidence_digests": tuple(
                    value["qualification_evidence_digests"]
                ),
            }
        )


@dataclass(frozen=True)
class BootstrapSeedAuthorizationV1:
    epoch_id: str
    bootstrap_package_digest: str
    lineage_registry_snapshot_digest: str
    qualification_registry_snapshot_digest: str
    owner_record_digest: str
    authorized_at: str

    def __post_init__(self):
        _text(self.epoch_id, "seed authorization epoch")
        for label, value in (
            ("seed package digest", self.bootstrap_package_digest),
            ("seed lineage registry digest", self.lineage_registry_snapshot_digest),
            ("seed qualification registry digest", self.qualification_registry_snapshot_digest),
            ("seed Owner record digest", self.owner_record_digest),
        ):
            _sha256(value, label)
        _timestamp(self.authorized_at, "seed authorization timestamp")

    def stable_mapping(self) -> dict:
        return {
            "epoch_id": self.epoch_id,
            "bootstrap_package_digest": self.bootstrap_package_digest,
            "lineage_registry_snapshot_digest": self.lineage_registry_snapshot_digest,
            "qualification_registry_snapshot_digest": (
                self.qualification_registry_snapshot_digest
            ),
            "owner_record_digest": self.owner_record_digest,
            "authorized_at": self.authorized_at,
        }

    @property
    def authorization_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapSeedAuthorizationV1":
        value = _strict_keys(raw, set(cls.__dataclass_fields__), "bootstrap seed authorization")
        return cls(**dict(value))


@dataclass(frozen=True)
class BootstrapCompletePayloadV1:
    epoch_id: str
    bootstrap_package_digest: str
    lineage_registry_snapshot_digest: str
    qualification_registry_snapshot_digest: str
    owner_seed_authorization_digest: str
    completed_at: str
    normal_mesh_policy_activated: bool = True
    protected_action_authorized: bool = False
    schema_version: str = BOOTSTRAP_COMPLETE_PAYLOAD_VERSION
    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        _text(self.epoch_id, "bootstrap completion epoch")
        for label, value in (
            ("completion package digest", self.bootstrap_package_digest),
            ("completion lineage registry digest", self.lineage_registry_snapshot_digest),
            ("completion qualification registry digest", self.qualification_registry_snapshot_digest),
            ("completion Owner authorization digest", self.owner_seed_authorization_digest),
        ):
            _sha256(value, label)
        _timestamp(self.completed_at, "bootstrap completion timestamp")
        if self.normal_mesh_policy_activated is not True:
            raise BootstrapGuardError("bootstrap completion did not activate normal policy")
        if self.protected_action_authorized is not False:
            raise BootstrapGuardError("bootstrap cannot authorize protected actions")
        if self.schema_version != BOOTSTRAP_COMPLETE_PAYLOAD_VERSION:
            raise BootstrapGuardError("bootstrap completion schema mismatch")
        if self.protocol_version != PROTOCOL_VERSION:
            raise BootstrapGuardError("bootstrap completion protocol mismatch")

    def stable_mapping(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "epoch_id": self.epoch_id,
            "bootstrap_package_digest": self.bootstrap_package_digest,
            "lineage_registry_snapshot_digest": self.lineage_registry_snapshot_digest,
            "qualification_registry_snapshot_digest": (
                self.qualification_registry_snapshot_digest
            ),
            "owner_seed_authorization_digest": self.owner_seed_authorization_digest,
            "completed_at": self.completed_at,
            "normal_mesh_policy_activated": self.normal_mesh_policy_activated,
            "protected_action_authorized": self.protected_action_authorized,
        }

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapCompletePayloadV1":
        raw = _strict_keys(
            raw,
            {
                "schema_version",
                "protocol_version",
                "epoch_id",
                "bootstrap_package_digest",
                "lineage_registry_snapshot_digest",
                "qualification_registry_snapshot_digest",
                "owner_seed_authorization_digest",
                "completed_at",
                "normal_mesh_policy_activated",
                "protected_action_authorized",
            },
            "bootstrap completion payload",
        )
        return cls(**dict(raw))

    @property
    def payload_digest(self) -> str:
        return canonical_digest(self.stable_mapping())


@dataclass(frozen=True)
class BootstrapEventV1:
    sequence_number: int
    previous_event_digest: str
    state: BootstrapStateV1
    payload_digest: str
    recorded_at: str
    reason_code: str
    schema_version: str = BOOTSTRAP_EVENT_SCHEMA_VERSION

    def __post_init__(self):
        _positive_u64(self.sequence_number, "bootstrap event sequence")
        _sha256(self.previous_event_digest, "previous bootstrap event digest")
        _sha256(self.payload_digest, "bootstrap event payload digest")
        if not isinstance(self.state, BootstrapStateV1):
            raise BootstrapGuardError("bootstrap event state is invalid")
        _timestamp(self.recorded_at, "bootstrap event timestamp")
        _text(self.reason_code, "bootstrap event reason")
        if self.schema_version != BOOTSTRAP_EVENT_SCHEMA_VERSION:
            raise BootstrapGuardError("bootstrap event schema mismatch")

    def stable_mapping(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "sequence_number": self.sequence_number,
            "previous_event_digest": self.previous_event_digest,
            "state": self.state.value,
            "payload_digest": self.payload_digest,
            "recorded_at": self.recorded_at,
            "reason_code": self.reason_code,
        }

    @property
    def event_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    def to_mapping(self) -> dict:
        return self.stable_mapping() | {"event_digest": self.event_digest}

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapEventV1":
        value = _strict_keys(
            raw,
            set(cls.__dataclass_fields__) | {"event_digest"},
            "bootstrap event",
        )
        event = cls(
            sequence_number=value["sequence_number"],
            previous_event_digest=value["previous_event_digest"],
            state=BootstrapStateV1(value["state"]),
            payload_digest=value["payload_digest"],
            recorded_at=value["recorded_at"],
            reason_code=value["reason_code"],
            schema_version=value["schema_version"],
        )
        if value["event_digest"] != event.event_digest:
            raise BootstrapGuardError("bootstrap event digest mismatch")
        return event


_ALLOWED_TRANSITIONS = {
    BootstrapStateV1.UNINITIALIZED: {
        BootstrapStateV1.OWNER_AUTHORIZED,
        BootstrapStateV1.ABORTED,
    },
    BootstrapStateV1.OWNER_AUTHORIZED: {
        BootstrapStateV1.MATERIAL_PINNED,
        BootstrapStateV1.ABORTED,
    },
    BootstrapStateV1.MATERIAL_PINNED: {
        BootstrapStateV1.HARNESS_INSPECTED,
        BootstrapStateV1.ABORTED,
    },
    BootstrapStateV1.HARNESS_INSPECTED: {
        BootstrapStateV1.EXECUTIONS_COMPLETE,
        BootstrapStateV1.ABORTED,
    },
    BootstrapStateV1.EXECUTIONS_COMPLETE: {
        BootstrapStateV1.SEED_PROPOSED,
        BootstrapStateV1.ABORTED,
    },
    BootstrapStateV1.SEED_PROPOSED: {
        BootstrapStateV1.COMPLETE,
        BootstrapStateV1.ABORTED,
    },
    BootstrapStateV1.COMPLETE: set(),
    BootstrapStateV1.ABORTED: set(),
}


@dataclass(frozen=True)
class BootstrapV1:
    events: tuple[BootstrapEventV1, ...] = ()
    authorization: BootstrapAuthorizationV1 | None = None
    material_pins: BootstrapMaterialPinsV1 | None = None
    inspections: tuple[HarnessInspectionRecordV1, ...] = ()
    executions: tuple[BootstrapQualificationExecutionV1, ...] = ()
    seed_proposal: BootstrapSeedProposalV1 | None = None
    seed_authorization: BootstrapSeedAuthorizationV1 | None = None
    complete_payload: BootstrapCompletePayloadV1 | None = None

    def __post_init__(self):
        previous = BOOTSTRAP_EVENT_GENESIS_DIGEST
        prior_state = BootstrapStateV1.UNINITIALIZED
        previous_recorded_at: datetime | None = None
        for sequence, event in enumerate(self.events, start=1):
            if event.sequence_number != sequence:
                raise BootstrapGuardError("bootstrap event sequence gap")
            if event.previous_event_digest != previous:
                raise BootstrapGuardError("bootstrap event previous digest mismatch")
            if event.state not in _ALLOWED_TRANSITIONS[prior_state]:
                raise BootstrapGuardError("bootstrap event transition is invalid")
            recorded_at = _timestamp(event.recorded_at, "bootstrap event timestamp")
            if previous_recorded_at is not None and recorded_at <= previous_recorded_at:
                raise BootstrapGuardError("bootstrap event timestamps are not strictly monotonic")
            previous_recorded_at = recorded_at
            previous = event.event_digest
            prior_state = event.state
        expected_presence = {
            BootstrapStateV1.UNINITIALIZED: (False, False, False, False, False, False, False),
            BootstrapStateV1.OWNER_AUTHORIZED: (True, False, False, False, False, False, False),
            BootstrapStateV1.MATERIAL_PINNED: (True, True, False, False, False, False, False),
            BootstrapStateV1.HARNESS_INSPECTED: (True, True, True, False, False, False, False),
            BootstrapStateV1.EXECUTIONS_COMPLETE: (True, True, True, True, False, False, False),
            BootstrapStateV1.SEED_PROPOSED: (True, True, True, True, True, False, False),
            BootstrapStateV1.COMPLETE: (True, True, True, True, True, True, True),
        }
        payload_state = self.state
        if payload_state is BootstrapStateV1.ABORTED:
            payload_state = (
                self.events[-2].state
                if len(self.events) > 1
                else BootstrapStateV1.UNINITIALIZED
            )
        observed = (
            self.authorization is not None,
            self.material_pins is not None,
            bool(self.inspections),
            bool(self.executions),
            self.seed_proposal is not None,
            self.seed_authorization is not None,
            self.complete_payload is not None,
        )
        if observed != expected_presence[payload_state]:
            raise BootstrapGuardError("bootstrap state payloads are inconsistent")
        self._validate_payload_bindings()

    def stable_mapping(self) -> dict:
        return {
            "schema_version": "BOOTSTRAP_JOURNAL_V1",
            "state": self.state.value,
            "head_digest": self.head_digest,
            "events": [event.to_mapping() for event in self.events],
            "authorization": (
                self.authorization.stable_mapping()
                if self.authorization is not None
                else None
            ),
            "material_pins": (
                self.material_pins.stable_mapping()
                if self.material_pins is not None
                else None
            ),
            "inspections": [item.stable_mapping() for item in self.inspections],
            "executions": [item.stable_mapping() for item in self.executions],
            "seed_proposal": (
                self.seed_proposal.stable_mapping()
                if self.seed_proposal is not None
                else None
            ),
            "seed_authorization": (
                self.seed_authorization.stable_mapping()
                if self.seed_authorization is not None
                else None
            ),
            "complete_payload": (
                self.complete_payload.stable_mapping()
                if self.complete_payload is not None
                else None
            ),
        }

    @property
    def journal_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    def to_mapping(self) -> dict:
        return self.stable_mapping() | {"journal_digest": self.journal_digest}

    @classmethod
    def from_mapping(cls, raw: object) -> "BootstrapV1":
        value = _strict_keys(
            raw,
            {
                "schema_version",
                "state",
                "head_digest",
                "events",
                "authorization",
                "material_pins",
                "inspections",
                "executions",
                "seed_proposal",
                "seed_authorization",
                "complete_payload",
                "journal_digest",
            },
            "bootstrap journal",
        )
        if value["schema_version"] != "BOOTSTRAP_JOURNAL_V1":
            raise BootstrapGuardError("bootstrap journal schema mismatch")
        for label in ("events", "inspections", "executions"):
            if not isinstance(value[label], list):
                raise BootstrapGuardError(f"bootstrap journal {label} must be an array")

        def optional(parser, item):
            return None if item is None else parser(item)

        journal = cls(
            events=tuple(BootstrapEventV1.from_mapping(item) for item in value["events"]),
            authorization=optional(
                BootstrapAuthorizationV1.from_mapping,
                value["authorization"],
            ),
            material_pins=optional(
                BootstrapMaterialPinsV1.from_mapping,
                value["material_pins"],
            ),
            inspections=tuple(
                HarnessInspectionRecordV1.from_mapping(item)
                for item in value["inspections"]
            ),
            executions=tuple(
                BootstrapQualificationExecutionV1.from_mapping(item)
                for item in value["executions"]
            ),
            seed_proposal=optional(
                BootstrapSeedProposalV1.from_mapping,
                value["seed_proposal"],
            ),
            seed_authorization=optional(
                BootstrapSeedAuthorizationV1.from_mapping,
                value["seed_authorization"],
            ),
            complete_payload=optional(
                BootstrapCompletePayloadV1.from_mapping,
                value["complete_payload"],
            ),
        )
        if value["state"] != journal.state.value:
            raise BootstrapGuardError("bootstrap journal state mismatch")
        if value["head_digest"] != journal.head_digest:
            raise BootstrapGuardError("bootstrap journal head mismatch")
        if value["journal_digest"] != journal.journal_digest:
            raise BootstrapGuardError("bootstrap journal digest mismatch")
        return journal

    def _validate_payload_bindings(self) -> None:
        """Reject forged snapshots whose retained payloads do not match events."""
        event_by_state = {event.state: event for event in self.events}
        if self.authorization is not None:
            event = event_by_state.get(BootstrapStateV1.OWNER_AUTHORIZED)
            if event is None or event.payload_digest != self.authorization.authorization_digest:
                raise BootstrapGuardError("bootstrap authorization event binding mismatch")
            if event.recorded_at != self.authorization.authorized_at:
                raise BootstrapGuardError("bootstrap authorization time binding mismatch")
            expiry = _timestamp(
                self.authorization.expires_at,
                "authorization expiry",
            )
            for authorized_event in self.events:
                if authorized_event.state is BootstrapStateV1.ABORTED:
                    continue
                if _timestamp(
                    authorized_event.recorded_at,
                    "authorized transition timestamp",
                ) > expiry:
                    raise BootstrapGuardError(
                        "bootstrap event occurs after authorization expiry"
                    )
        if self.material_pins is not None:
            if self.authorization is None:
                raise BootstrapGuardError("bootstrap material authorization is missing")
            if (
                self.material_pins.epoch_id != self.authorization.epoch_id
                or self.material_pins.authorization_digest
                != self.authorization.authorization_digest
            ):
                raise BootstrapGuardError("bootstrap material authorization binding mismatch")
            event = event_by_state.get(BootstrapStateV1.MATERIAL_PINNED)
            if event is None or event.payload_digest != self.material_pins.material_pins_digest:
                raise BootstrapGuardError("bootstrap material event binding mismatch")
            if event.recorded_at != self.material_pins.pinned_at:
                raise BootstrapGuardError("bootstrap material time binding mismatch")
        if self.inspections:
            event = event_by_state.get(BootstrapStateV1.HARNESS_INSPECTED)
            expected_digest = self._inspection_set_digest(self.inspections)
            if event is None or event.payload_digest != expected_digest:
                raise BootstrapGuardError("bootstrap inspection event binding mismatch")
            if event.recorded_at != max(item.inspected_at for item in self.inspections):
                raise BootstrapGuardError("bootstrap inspection time binding mismatch")
        if self.executions:
            event = event_by_state.get(BootstrapStateV1.EXECUTIONS_COMPLETE)
            expected_digest = self._execution_set_digest(self.executions)
            if event is None or event.payload_digest != expected_digest:
                raise BootstrapGuardError("bootstrap execution event binding mismatch")
            if event.recorded_at != max(item.completed_at for item in self.executions):
                raise BootstrapGuardError("bootstrap execution time binding mismatch")
        if self.seed_proposal is not None:
            event = event_by_state.get(BootstrapStateV1.SEED_PROPOSED)
            if event is None or event.payload_digest != self.seed_proposal.proposal_digest:
                raise BootstrapGuardError("bootstrap seed proposal event binding mismatch")
            if event.recorded_at != self.seed_proposal.proposed_at:
                raise BootstrapGuardError("bootstrap seed proposal time binding mismatch")
        if self.complete_payload is not None:
            proposal = self.seed_proposal
            seed_authorization = self.seed_authorization
            if proposal is None or seed_authorization is None:
                raise BootstrapGuardError("bootstrap completion trust inputs are missing")
            if (
                seed_authorization.epoch_id != proposal.epoch_id
                or seed_authorization.bootstrap_package_digest
                != proposal.bootstrap_package_digest
                or seed_authorization.lineage_registry_snapshot_digest
                != proposal.lineage_registry_snapshot_digest
                or seed_authorization.qualification_registry_snapshot_digest
                != proposal.qualification_registry_snapshot_digest
            ):
                raise BootstrapGuardError("Owner seed authorization binding mismatch")
            proposal_time = _timestamp(proposal.proposed_at, "seed proposal timestamp")
            seed_authorized_at = _timestamp(
                seed_authorization.authorized_at,
                "seed authorization timestamp",
            )
            completion_time = _timestamp(
                self.complete_payload.completed_at,
                "bootstrap completion timestamp",
            )
            if not proposal_time < seed_authorized_at < completion_time:
                raise BootstrapGuardError(
                    "bootstrap completion trust timestamps are not strictly monotonic"
                )
            self._assert_authorization_valid_at(
                seed_authorization.authorized_at,
                "seed authorization",
            )
            payload = self.complete_payload
            if (
                payload.epoch_id != proposal.epoch_id
                or payload.bootstrap_package_digest != proposal.bootstrap_package_digest
                or payload.lineage_registry_snapshot_digest
                != proposal.lineage_registry_snapshot_digest
                or payload.qualification_registry_snapshot_digest
                != proposal.qualification_registry_snapshot_digest
                or payload.owner_seed_authorization_digest
                != seed_authorization.authorization_digest
            ):
                raise BootstrapGuardError("bootstrap completion payload binding mismatch")
            event = event_by_state.get(BootstrapStateV1.COMPLETE)
            if event is None or event.payload_digest != payload.payload_digest:
                raise BootstrapGuardError("bootstrap completion event binding mismatch")
            if event.recorded_at != payload.completed_at:
                raise BootstrapGuardError("bootstrap completion time binding mismatch")

    def _inspection_set_digest(
        self,
        inspections: tuple[HarnessInspectionRecordV1, ...],
    ) -> str:
        if self.authorization is None:
            raise BootstrapGuardError("bootstrap authorization is missing")
        return canonical_digest({
            "epoch_id": self.authorization.epoch_id,
            "inspection_digests": sorted(
                item.inspection_digest for item in inspections
            ),
        })

    def _execution_set_digest(
        self,
        executions: tuple[BootstrapQualificationExecutionV1, ...],
    ) -> str:
        if self.authorization is None:
            raise BootstrapGuardError("bootstrap authorization is missing")
        return canonical_digest({
            "epoch_id": self.authorization.epoch_id,
            "executions": sorted(
                canonical_digest(item.stable_mapping()) for item in executions
            ),
        })

    @property
    def state(self) -> BootstrapStateV1:
        if not self.events:
            return BootstrapStateV1.UNINITIALIZED
        return self.events[-1].state

    @property
    def head_digest(self) -> str:
        if not self.events:
            return BOOTSTRAP_EVENT_GENESIS_DIGEST
        return self.events[-1].event_digest

    def _append(
        self,
        state: BootstrapStateV1,
        payload_digest: str,
        recorded_at: str,
        reason_code: str,
        **changes,
    ) -> "BootstrapV1":
        if state not in _ALLOWED_TRANSITIONS[self.state]:
            raise BootstrapGuardError("bootstrap transition is not allowed")
        transition_time = _timestamp(recorded_at, "bootstrap transition timestamp")
        if self.events and transition_time <= _timestamp(
            self.events[-1].recorded_at,
            "previous bootstrap event timestamp",
        ):
            raise BootstrapGuardError("bootstrap transition timestamp is not strictly monotonic")
        event = BootstrapEventV1(
            sequence_number=len(self.events) + 1,
            previous_event_digest=self.head_digest,
            state=state,
            payload_digest=payload_digest,
            recorded_at=recorded_at,
            reason_code=reason_code,
        )
        return replace(self, events=self.events + (event,), **changes)

    def _assert_authorization_valid_at(self, value: str, label: str) -> None:
        authorization = self.authorization
        if authorization is None:
            raise BootstrapGuardError("bootstrap authorization is missing")
        if _timestamp(value, label) > _timestamp(
            authorization.expires_at,
            "authorization expiry",
        ):
            raise BootstrapGuardError(f"bootstrap authorization expired before {label}")

    def _guard_abort_time(self, attempted_at: str | None) -> str:
        """Produce a valid, monotonic journal time for a failed transition."""
        if attempted_at is not None:
            try:
                candidate = _timestamp(attempted_at, "guard failure timestamp")
            except BootstrapGuardError:
                candidate = (
                    _timestamp(
                        self.events[-1].recorded_at,
                        "previous bootstrap event timestamp",
                    ) + timedelta(microseconds=1)
                    if self.events
                    else datetime.now(timezone.utc)
                )
        elif self.events:
            candidate = _timestamp(
                self.events[-1].recorded_at,
                "previous bootstrap event timestamp",
            ) + timedelta(microseconds=1)
        else:
            # A typed transition always carries a time.  This branch only
            # protects a future empty-input transition API.
            candidate = datetime.now(timezone.utc)
        if self.events:
            minimum = _timestamp(
                self.events[-1].recorded_at,
                "previous bootstrap event timestamp",
            ) + timedelta(microseconds=1)
            candidate = max(candidate, minimum)
        return candidate.isoformat()

    def _fail_closed(
        self,
        *,
        operation: str,
        attempted_payload_digest: str,
        attempted_at: str | None,
        error: BootstrapGuardError,
    ) -> "BootstrapV1":
        if self.state in {BootstrapStateV1.COMPLETE, BootstrapStateV1.ABORTED}:
            raise BootstrapGuardError("terminal bootstrap epoch cannot transition") from error
        evidence_digest = canonical_digest({
            "schema_version": "BOOTSTRAP_GUARD_FAILURE_V1",
            "epoch_id": (
                self.authorization.epoch_id
                if self.authorization is not None
                else "UNAUTHORIZED_EPOCH"
            ),
            "operation": operation,
            "prior_state": self.state.value,
            "prior_head_digest": self.head_digest,
            "attempted_payload_digest": attempted_payload_digest,
            "guard_error": str(error),
        })
        return self._append(
            BootstrapStateV1.ABORTED,
            evidence_digest,
            self._guard_abort_time(attempted_at),
            f"{_GUARD_ABORT_REASON_PREFIX}_{operation}",
        )

    @staticmethod
    def _invalid_attempt_digest(operation: str, value: object) -> str:
        return canonical_digest({
            "schema_version": "BOOTSTRAP_INVALID_ATTEMPT_V1",
            "operation": operation,
            "input_type": type(value).__name__,
        })

    def abort(self, *, reason_code: str, evidence_digest: str, aborted_at: str) -> "BootstrapV1":
        if self.state in {BootstrapStateV1.COMPLETE, BootstrapStateV1.ABORTED}:
            raise BootstrapGuardError("terminal bootstrap epoch cannot abort again")
        return self._append(
            BootstrapStateV1.ABORTED,
            _sha256(evidence_digest, "bootstrap abort evidence"),
            aborted_at,
            reason_code,
        )

    def authorize(self, authorization: BootstrapAuthorizationV1) -> "BootstrapV1":
        attempted_digest = (
            authorization.authorization_digest
            if isinstance(authorization, BootstrapAuthorizationV1)
            else self._invalid_attempt_digest("AUTHORIZE", authorization)
        )
        attempted_at = (
            authorization.authorized_at
            if isinstance(authorization, BootstrapAuthorizationV1)
            else None
        )
        try:
            if not isinstance(authorization, BootstrapAuthorizationV1):
                raise BootstrapGuardError("bootstrap authorization type is invalid")
            return self._append(
                BootstrapStateV1.OWNER_AUTHORIZED,
                authorization.authorization_digest,
                authorization.authorized_at,
                "EXACT_OWNER_AUTHORIZATION_ACCEPTED",
                authorization=authorization,
            )
        except BootstrapGuardError as error:
            return self._fail_closed(
                operation="AUTHORIZE",
                attempted_payload_digest=attempted_digest,
                attempted_at=attempted_at,
                error=error,
            )

    def retry_with_new_epoch(
        self,
        authorization: BootstrapAuthorizationV1,
    ) -> "BootstrapV1":
        """Start a fresh journal after an aborted epoch with fresh Owner authority.

        The aborted epoch remains immutable.  Reusing its epoch identifier is
        forbidden even when every other authorization field changes.
        """
        if self.state is not BootstrapStateV1.ABORTED:
            raise BootstrapGuardError("only an aborted bootstrap can start a retry epoch")
        if self.authorization is not None and (
            authorization.epoch_id == self.authorization.epoch_id
        ):
            raise BootstrapGuardError("bootstrap retry requires a new epoch id")
        retry = BootstrapV1().authorize(authorization)
        if retry.state is not BootstrapStateV1.OWNER_AUTHORIZED:
            raise BootstrapGuardError("fresh bootstrap retry authorization failed")
        return retry

    def pin_material(self, pins: BootstrapMaterialPinsV1) -> "BootstrapV1":
        attempted_digest = (
            pins.material_pins_digest
            if isinstance(pins, BootstrapMaterialPinsV1)
            else self._invalid_attempt_digest("PIN_MATERIAL", pins)
        )
        attempted_at = pins.pinned_at if isinstance(pins, BootstrapMaterialPinsV1) else None
        try:
            if not isinstance(pins, BootstrapMaterialPinsV1):
                raise BootstrapGuardError("bootstrap material pins type is invalid")
            authorization = self.authorization
            if authorization is None:
                raise BootstrapGuardError("bootstrap authorization is missing")
            if pins.epoch_id != authorization.epoch_id:
                raise BootstrapGuardError("material epoch mismatch")
            if pins.authorization_digest != authorization.authorization_digest:
                raise BootstrapGuardError("material authorization digest mismatch")
            self._assert_authorization_valid_at(pins.pinned_at, "material pin")
            return self._append(
                BootstrapStateV1.MATERIAL_PINNED,
                pins.material_pins_digest,
                pins.pinned_at,
                "IMMUTABLE_MATERIAL_PINNED",
                material_pins=pins,
            )
        except BootstrapGuardError as error:
            return self._fail_closed(
                operation="PIN_MATERIAL",
                attempted_payload_digest=attempted_digest,
                attempted_at=attempted_at,
                error=error,
            )

    def inspect_harness(
        self,
        inspections: tuple[HarnessInspectionRecordV1, ...],
    ) -> "BootstrapV1":
        typed_inspections = (
            isinstance(inspections, tuple)
            and all(isinstance(item, HarnessInspectionRecordV1) for item in inspections)
        )
        attempted_digest = (
            canonical_digest({
                "inspection_digests": sorted(
                    item.inspection_digest for item in inspections
                ),
            })
            if typed_inspections
            else self._invalid_attempt_digest("INSPECT_HARNESS", inspections)
        )
        attempted_at = (
            max((item.inspected_at for item in inspections), default=None)
            if typed_inspections
            else None
        )
        try:
            if not typed_inspections:
                raise BootstrapGuardError("bootstrap inspections type is invalid")
            authorization = self.authorization
            pins = self.material_pins
            if authorization is None or pins is None:
                raise BootstrapGuardError("bootstrap material is not pinned")
            self._validate_external_set(tuple(item.identity for item in inspections))
            if len(inspections) < 2:
                raise BootstrapGuardError("two harness inspections are required")
            if len({item.inspection_id for item in inspections}) != len(inspections):
                raise BootstrapGuardError("duplicate harness inspection id")
            if len({item.provider_execution_receipt_digest for item in inspections}) != len(
                inspections
            ):
                raise BootstrapGuardError("duplicate harness inspection receipt")
            prior_time = _timestamp(self.events[-1].recorded_at, "prior event timestamp")
            for item in inspections:
                inspected_at = _timestamp(item.inspected_at, "inspection timestamp")
                if inspected_at <= prior_time:
                    raise BootstrapGuardError("inspection timestamp is not after material pin")
                self._assert_authorization_valid_at(item.inspected_at, "harness inspection")
                if item.epoch_id != authorization.epoch_id:
                    raise BootstrapGuardError("inspection epoch mismatch")
                if item.harness_implementation_digest != authorization.harness_implementation_digest:
                    raise BootstrapGuardError("inspection harness digest mismatch")
                if item.configuration_digest != authorization.configuration_digest:
                    raise BootstrapGuardError("inspection configuration digest mismatch")
                if item.identity.identity_record_digest in (
                    authorization.disallowed_contributor_identity_digests
                ):
                    raise BootstrapGuardError("harness contributor inspected its own work")
            recorded_at = max(item.inspected_at for item in inspections)
            digest = self._inspection_set_digest(inspections)
            return self._append(
                BootstrapStateV1.HARNESS_INSPECTED,
                digest,
                recorded_at,
                "TWO_EXTERNAL_LINEAGES_INSPECTED_HARNESS",
                inspections=tuple(sorted(inspections, key=lambda item: item.inspection_id)),
            )
        except BootstrapGuardError as error:
            return self._fail_closed(
                operation="INSPECT_HARNESS",
                attempted_payload_digest=attempted_digest,
                attempted_at=attempted_at,
                error=error,
            )

    def complete_executions(
        self,
        executions: tuple[BootstrapQualificationExecutionV1, ...],
    ) -> "BootstrapV1":
        typed_executions = (
            isinstance(executions, tuple)
            and all(
                isinstance(item, BootstrapQualificationExecutionV1)
                for item in executions
            )
        )
        attempted_digest = (
            canonical_digest({
                "execution_digests": sorted(
                    canonical_digest(item.stable_mapping()) for item in executions
                ),
            })
            if typed_executions
            else self._invalid_attempt_digest("COMPLETE_EXECUTIONS", executions)
        )
        attempted_at = (
            max((item.completed_at for item in executions), default=None)
            if typed_executions
            else None
        )
        try:
            if not typed_executions:
                raise BootstrapGuardError("bootstrap executions type is invalid")
            authorization = self.authorization
            pins = self.material_pins
            if authorization is None or pins is None:
                raise BootstrapGuardError("bootstrap prerequisites are missing")
            if len(executions) < 2:
                raise BootstrapGuardError("two qualification executions are required")
            self._validate_external_set(tuple(item.identity for item in executions))
            if len({item.attempt_id for item in executions}) != len(executions):
                raise BootstrapGuardError("duplicate qualification attempt id")
            for label, values in (
                ("qualification nonce", (item.nonce for item in executions)),
                (
                    "qualification provider receipt",
                    (item.provider_execution_receipt_digest for item in executions),
                ),
                (
                    "qualification evidence digest",
                    (item.qualification_evidence_digest for item in executions),
                ),
            ):
                materialized = tuple(values)
                if len(materialized) != len(set(materialized)):
                    raise BootstrapGuardError(f"duplicate {label}")
            prior_time = _timestamp(self.events[-1].recorded_at, "prior event timestamp")
            for item in executions:
                completed_at = _timestamp(item.completed_at, "execution timestamp")
                if completed_at <= prior_time:
                    raise BootstrapGuardError("execution timestamp is not after inspection")
                self._assert_authorization_valid_at(
                    item.completed_at,
                    "qualification execution",
                )
                if item.epoch_id != authorization.epoch_id:
                    raise BootstrapGuardError("qualification execution epoch mismatch")
                if item.harness_implementation_digest != authorization.harness_implementation_digest:
                    raise BootstrapGuardError("qualification harness digest mismatch")
                if item.configuration_digest != authorization.configuration_digest:
                    raise BootstrapGuardError("qualification config digest mismatch")
                if item.public_fixture_manifest_digest != pins.public_fixture_manifest_digest:
                    raise BootstrapGuardError("qualification public manifest mismatch")
                if item.sealed_fixture_manifest_digest != pins.sealed_fixture_manifest_digest:
                    raise BootstrapGuardError("qualification sealed manifest mismatch")
                if (
                    item.verdict is not BootstrapExecutionVerdictV1.PASS
                    or item.mandatory_fixtures_complete is not True
                    or item.mandatory_hidden_blocking_false_passes != 0
                    or item.label_leakage_detected
                    or item.identity_ambiguous
                    or item.unexpected_fallback
                ):
                    raise BootstrapGuardError("qualification execution guard failed")
            completed_at = max(item.completed_at for item in executions)
            payload_digest = self._execution_set_digest(executions)
            return self._append(
                BootstrapStateV1.EXECUTIONS_COMPLETE,
                payload_digest,
                completed_at,
                "TWO_PINNED_EXTERNAL_EXECUTIONS_COMPLETE",
                executions=tuple(sorted(executions, key=lambda item: item.attempt_id)),
            )
        except BootstrapGuardError as error:
            return self._fail_closed(
                operation="COMPLETE_EXECUTIONS",
                attempted_payload_digest=attempted_digest,
                attempted_at=attempted_at,
                error=error,
            )

    def expected_bootstrap_package_digest(
        self,
        *,
        lineage_registry_snapshot_digest: str,
        qualification_registry_snapshot_digest: str,
    ) -> str:
        if self.state is not BootstrapStateV1.EXECUTIONS_COMPLETE:
            raise BootstrapGuardError("bootstrap executions are not complete")
        assert self.authorization is not None
        assert self.material_pins is not None
        return canonical_digest({
            "schema_version": "BOOTSTRAP_PACKAGE_V1",
            "epoch_id": self.authorization.epoch_id,
            "authorization_digest": self.authorization.authorization_digest,
            "material_pins_digest": self.material_pins.material_pins_digest,
            "inspection_digests": sorted(
                item.inspection_digest for item in self.inspections
            ),
            "execution_digests": sorted(
                canonical_digest(item.stable_mapping()) for item in self.executions
            ),
            "lineage_registry_snapshot_digest": _sha256(
                lineage_registry_snapshot_digest,
                "lineage registry digest",
            ),
            "qualification_registry_snapshot_digest": _sha256(
                qualification_registry_snapshot_digest,
                "qualification registry digest",
            ),
        })

    def propose_seed(self, proposal: BootstrapSeedProposalV1) -> "BootstrapV1":
        attempted_digest = (
            proposal.proposal_digest
            if isinstance(proposal, BootstrapSeedProposalV1)
            else self._invalid_attempt_digest("PROPOSE_SEED", proposal)
        )
        attempted_at = proposal.proposed_at if isinstance(proposal, BootstrapSeedProposalV1) else None
        try:
            if not isinstance(proposal, BootstrapSeedProposalV1):
                raise BootstrapGuardError("bootstrap seed proposal type is invalid")
            if self.authorization is None:
                raise BootstrapGuardError("bootstrap authorization is missing")
            self._assert_authorization_valid_at(proposal.proposed_at, "seed proposal")
            if proposal.epoch_id != self.authorization.epoch_id:
                raise BootstrapGuardError("seed proposal epoch mismatch")
            expected_evidence = tuple(sorted(
                item.qualification_evidence_digest for item in self.executions
            ))
            if proposal.qualification_evidence_digests != expected_evidence:
                raise BootstrapGuardError("seed proposal evidence set mismatch")
            expected_package = self.expected_bootstrap_package_digest(
                lineage_registry_snapshot_digest=proposal.lineage_registry_snapshot_digest,
                qualification_registry_snapshot_digest=(
                    proposal.qualification_registry_snapshot_digest
                ),
            )
            if proposal.bootstrap_package_digest != expected_package:
                raise BootstrapGuardError("bootstrap package digest mismatch")
            return self._append(
                BootstrapStateV1.SEED_PROPOSED,
                proposal.proposal_digest,
                proposal.proposed_at,
                "CANONICAL_SEED_PROPOSED",
                seed_proposal=proposal,
            )
        except BootstrapGuardError as error:
            return self._fail_closed(
                operation="PROPOSE_SEED",
                attempted_payload_digest=attempted_digest,
                attempted_at=attempted_at,
                error=error,
            )

    def complete(
        self,
        seed_authorization: BootstrapSeedAuthorizationV1,
        *,
        completed_at: str,
    ) -> "BootstrapV1":
        attempted_digest = (
            canonical_digest({
                "seed_authorization_digest": seed_authorization.authorization_digest,
                "completed_at": completed_at,
            })
            if isinstance(seed_authorization, BootstrapSeedAuthorizationV1)
            else self._invalid_attempt_digest("COMPLETE", seed_authorization)
        )
        try:
            if not isinstance(seed_authorization, BootstrapSeedAuthorizationV1):
                raise BootstrapGuardError("bootstrap seed authorization type is invalid")
            proposal = self.seed_proposal
            if proposal is None:
                raise BootstrapGuardError("bootstrap seed proposal is missing")
            self._assert_authorization_valid_at(
                seed_authorization.authorized_at,
                "seed authorization",
            )
            self._assert_authorization_valid_at(completed_at, "bootstrap completion")
            proposal_time = _timestamp(proposal.proposed_at, "seed proposal timestamp")
            seed_authorized_at = _timestamp(
                seed_authorization.authorized_at,
                "seed authorization timestamp",
            )
            completion_time = _timestamp(completed_at, "bootstrap completion timestamp")
            if seed_authorized_at <= proposal_time:
                raise BootstrapGuardError("seed authorization is not after seed proposal")
            if completion_time <= seed_authorized_at:
                raise BootstrapGuardError("bootstrap completion is not after seed authorization")
            if (
                seed_authorization.epoch_id != proposal.epoch_id
                or seed_authorization.bootstrap_package_digest
                != proposal.bootstrap_package_digest
                or seed_authorization.lineage_registry_snapshot_digest
                != proposal.lineage_registry_snapshot_digest
                or seed_authorization.qualification_registry_snapshot_digest
                != proposal.qualification_registry_snapshot_digest
            ):
                raise BootstrapGuardError("Owner seed authorization binding mismatch")
            payload = BootstrapCompletePayloadV1(
                epoch_id=proposal.epoch_id,
                bootstrap_package_digest=proposal.bootstrap_package_digest,
                lineage_registry_snapshot_digest=proposal.lineage_registry_snapshot_digest,
                qualification_registry_snapshot_digest=(
                    proposal.qualification_registry_snapshot_digest
                ),
                owner_seed_authorization_digest=seed_authorization.authorization_digest,
                completed_at=completed_at,
            )
            return self._append(
                BootstrapStateV1.COMPLETE,
                payload.payload_digest,
                completed_at,
                "EXACT_OWNER_SEED_AUTHORIZED_AND_POLICY_ACTIVATED",
                seed_authorization=seed_authorization,
                complete_payload=payload,
            )
        except BootstrapGuardError as error:
            return self._fail_closed(
                operation="COMPLETE",
                attempted_payload_digest=attempted_digest,
                attempted_at=completed_at,
                error=error,
            )

    def _validate_external_set(
        self,
        identities: tuple[BootstrapObservedIdentityV1, ...],
    ) -> None:
        authorization = self.authorization
        if authorization is None:
            raise BootstrapGuardError("bootstrap authorization is missing")
        providers = {item.provider_principal for item in identities}
        lineages = {item.foundation_lineage_class for item in identities}
        if len(providers) < 2 or len(lineages) < 2:
            raise BootstrapGuardError("two distinct provider and foundation lineages required")
        for identity in identities:
            if identity.provider_principal not in authorization.allowed_provider_principals:
                raise BootstrapGuardError("provider is outside bootstrap authorization")
            if (
                identity.authenticated_adapter_principal
                not in authorization.allowed_adapter_principals
            ):
                raise BootstrapGuardError("adapter is outside bootstrap authorization")
            if identity.identity_record_digest in (
                authorization.disallowed_contributor_identity_digests
            ):
                raise BootstrapGuardError("bootstrap contributor cannot self-qualify")
