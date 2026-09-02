"""Strict, content-addressed Review Mesh registries.

G0-B keeps the durable registry schema richer than the G0-A decision views.
Compilation into the G0-A types is explicit and is permitted only for an
exact, active, current identity.  No provider call, ledger write, bootstrap
transition, or protected action is performed by this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re

from .review_mesh_decisions import (
    LineageApprovalState,
    LineageRegistryEntryV1,
    LineageRegistrySnapshotV1,
)
from .review_mesh_protocol import (
    ExecutionLocality,
    PROTOCOL_VERSION,
    ReviewerClass,
    RiskLevel,
    canonical_digest,
)
from .review_mesh_quorum import QualificationEligibilityV1
from .review_mesh_bootstrap import BootstrapCompletePayloadV1


LINEAGE_REGISTRY_VERSION = "LINEAGE_REGISTRY_V1"
QUALIFICATION_REGISTRY_VERSION = "QUALIFICATION_REGISTRY_V1"
GENESIS_SNAPSHOT_DIGEST = "0" * 64

_SHA256 = re.compile(r"[a-f0-9]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/+@-]{1,256}")
_NON_EXACT_MODEL_TOKEN = re.compile(
    r"(?:^|[._:/+@-])"
    r"(alias|ambiguous|latest|preview|auto|default|unknown|unpinned)"
    r"(?:$|[._:/+@-])",
    re.IGNORECASE,
)
_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?\+00:00"
)


def _require_mapping(value: object, keys: set[str], label: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} fields are invalid")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _require_identifier(value: object, label: str) -> str:
    value = _require_string(value, label)
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _require_sha256(value: object, label: str) -> str:
    value = _require_string(value, label)
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_positive_u64(value: object, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value >= 2**64
    ):
        raise ValueError(f"{label} must be a positive unsigned 64-bit integer")
    return value


def _require_optional_positive_u64(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _require_positive_u64(value, label)


def _parse_timestamp(value: object, label: str) -> datetime:
    value = _require_string(value, label)
    if not _UTC_TIMESTAMP.fullmatch(value):
        raise ValueError(f"{label} must be canonical RFC3339 UTC ending in +00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must be canonical RFC3339 UTC") from error
    return parsed


def _require_utc_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value


def _optional_timestamp(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    return _parse_timestamp(value, label)


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _enum(value: object, enum_type: type[Enum], label: str):
    value = _require_string(value, label)
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error


def _identifier_tuple(
    values: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValueError(f"{label} must be a tuple")
    if not values and not allow_empty:
        raise ValueError(f"{label} must not be empty")
    normalized = tuple(
        _require_identifier(value, f"{label} item")
        for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} contains duplicates")
    return tuple(sorted(normalized))


def _enum_tuple(
    values: object,
    enum_type: type[Enum],
    label: str,
) -> tuple:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{label} must be a non-empty tuple")
    for value in values:
        if not isinstance(value, enum_type):
            raise ValueError(f"{label} item is invalid")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} contains duplicates")
    return tuple(sorted(values, key=lambda item: item.value))


def _tuple_from_list(value: object, label: str) -> tuple:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return tuple(value)


def _is_exact_model_id(value: str) -> bool:
    return _NON_EXACT_MODEL_TOKEN.search(value) is None


def _require_exact_identity_component(value: str, label: str) -> None:
    if not _is_exact_model_id(value):
        raise ValueError(f"{label} is ambiguous or unpinned")


def _validate_egress(
    *,
    data_egress_permitted: bool,
    actual_egress_destination: str,
) -> None:
    if data_egress_permitted and actual_egress_destination == "NONE":
        raise ValueError("permitted data egress requires an exact destination")
    if not data_egress_permitted and actual_egress_destination != "NONE":
        raise ValueError("denied data egress must use destination NONE")


def _validate_activation(
    *,
    activation_state: "RegistryActivationStateV1",
    created_at: str,
    reviewed_at: str,
    activated_at: str | None,
    expires_at: str,
    activation_record_digest: str | None,
    ledger_sequence: int | None,
    label: str,
) -> tuple[datetime, datetime | None, datetime]:
    created = _parse_timestamp(created_at, f"{label} creation timestamp")
    reviewed = _parse_timestamp(reviewed_at, f"{label} review timestamp")
    activated = _optional_timestamp(
        activated_at,
        f"{label} activation timestamp",
    )
    expires = _parse_timestamp(expires_at, f"{label} expiry timestamp")

    if reviewed < created:
        raise ValueError(f"{label} review precedes creation")
    if expires <= reviewed:
        raise ValueError(f"{label} expiry must follow review")

    if activation_record_digest is not None:
        _require_sha256(
            activation_record_digest,
            f"{label} activation record digest",
        )
    _require_optional_positive_u64(ledger_sequence, f"{label} ledger sequence")

    binding_values = (
        activated is not None,
        activation_record_digest is not None,
        ledger_sequence is not None,
    )
    if len(set(binding_values)) != 1:
        raise ValueError(f"{label} activation binding must be all present or all absent")

    if activation_state is RegistryActivationStateV1.PROPOSED and any(binding_values):
        raise ValueError(f"proposed {label} cannot carry activation binding")

    if activation_state is RegistryActivationStateV1.ACTIVE:
        if activated is None:
            raise ValueError(f"active {label} lacks activation timestamp")
        if activated < reviewed or expires <= activated:
            raise ValueError(f"active {label} has invalid activation interval")
        if activation_record_digest is None or ledger_sequence is None:
            raise ValueError(f"active {label} lacks durable activation binding")

    if activated is not None and activated < reviewed:
        raise ValueError(f"{label} activation precedes independent review")

    return created, activated, expires


class IdentityPrecisionV1(str, Enum):
    EXACT = "EXACT"
    ALIAS = "ALIAS"
    AMBIGUOUS = "AMBIGUOUS"
    LATEST = "LATEST"


class FallbackStateV1(str, Enum):
    NO_FALLBACK = "NO_FALLBACK"
    POLICY_PERMITTED_FALLBACK = "POLICY_PERMITTED_FALLBACK"
    UNEXPECTED_FALLBACK = "UNEXPECTED_FALLBACK"
    AMBIGUOUS_FALLBACK = "AMBIGUOUS_FALLBACK"


class RegistryActivationStateV1(str, Enum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class QualificationStatusV1(str, Enum):
    REGISTERED_NOT_QUALIFIED = "REGISTERED_NOT_QUALIFIED"
    QUALIFICATION_RUNNING = "QUALIFICATION_RUNNING"
    QUALIFIED_P3 = "QUALIFIED_P3"
    QUALIFIED_P2 = "QUALIFIED_P2"
    QUALIFIED_STRONG_P1 = "QUALIFIED_STRONG_P1"
    QUALIFIED_STRONG_P0 = "QUALIFIED_STRONG_P0"
    SUSPENDED = "SUSPENDED"
    REQUALIFICATION_REQUIRED = "REQUALIFICATION_REQUIRED"
    REVOKED = "REVOKED"


_QUALIFIED_STATUS_CLASS = {
    QualificationStatusV1.QUALIFIED_P3: ReviewerClass.P3,
    QualificationStatusV1.QUALIFIED_P2: ReviewerClass.P2,
    QualificationStatusV1.QUALIFIED_STRONG_P1: ReviewerClass.STRONG_P1,
    QualificationStatusV1.QUALIFIED_STRONG_P0: ReviewerClass.STRONG_P0,
}


@dataclass(frozen=True)
class PermittedFallbackIdentityV1:
    """Closed, exact identity permitted as a fallback for one requested route."""

    authenticated_adapter_principal: str
    provider_principal: str
    provider_account_scope: str
    serving_backend: str
    endpoint_class: str
    actual_model_id: str

    def __post_init__(self):
        for label, value in (
            ("fallback adapter principal", self.authenticated_adapter_principal),
            ("fallback provider principal", self.provider_principal),
            ("fallback provider account scope", self.provider_account_scope),
            ("fallback serving backend", self.serving_backend),
            ("fallback endpoint class", self.endpoint_class),
            ("fallback actual model id", self.actual_model_id),
        ):
            _require_identifier(value, label)
        _require_exact_identity_component(
            self.actual_model_id,
            "fallback actual model id",
        )

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (
            self.provider_principal,
            self.provider_account_scope,
            self.serving_backend,
            self.endpoint_class,
            self.authenticated_adapter_principal,
            self.actual_model_id,
        )

    def stable_mapping(self) -> dict:
        return {
            "authenticated_adapter_principal": self.authenticated_adapter_principal,
            "provider_principal": self.provider_principal,
            "provider_account_scope": self.provider_account_scope,
            "serving_backend": self.serving_backend,
            "endpoint_class": self.endpoint_class,
            "actual_model_id": self.actual_model_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping) -> "PermittedFallbackIdentityV1":
        raw = _require_mapping(
            value,
            {
                "authenticated_adapter_principal",
                "provider_principal",
                "provider_account_scope",
                "serving_backend",
                "endpoint_class",
                "actual_model_id",
            },
            "permitted fallback identity",
        )
        return cls(
            authenticated_adapter_principal=_require_string(
                raw["authenticated_adapter_principal"],
                "fallback adapter principal",
            ),
            provider_principal=_require_string(
                raw["provider_principal"],
                "fallback provider principal",
            ),
            provider_account_scope=_require_string(
                raw["provider_account_scope"],
                "fallback provider account scope",
            ),
            serving_backend=_require_string(
                raw["serving_backend"],
                "fallback serving backend",
            ),
            endpoint_class=_require_string(
                raw["endpoint_class"],
                "fallback endpoint class",
            ),
            actual_model_id=_require_string(
                raw["actual_model_id"],
                "fallback actual model id",
            ),
        )


@dataclass(frozen=True)
class LineageIdentityEntryV1:
    reviewer_registry_id: str

    authenticated_adapter_principal: str
    allowed_authentication_methods: tuple[str, ...]
    provider_principal: str
    provider_account_scope: str
    serving_backend: str
    endpoint_class: str

    requested_model_aliases: tuple[str, ...]
    actual_model_id: str
    identity_precision: IdentityPrecisionV1
    permitted_fallback_identities: tuple[PermittedFallbackIdentityV1, ...]

    foundation_model: str
    foundation_revision: str
    foundation_lineage_class: str
    correlation_group: str
    hosted_copy_relationship: str
    derivative_relationship: str

    execution_locality: ExecutionLocality
    data_egress_permitted: bool
    actual_egress_destination: str

    eligible_reviewer_classes: tuple[ReviewerClass, ...]
    eligible_risk_levels: tuple[RiskLevel, ...]

    lineage_evidence_digest: str
    approval_state: LineageApprovalState
    activation_state: RegistryActivationStateV1
    requalification_conditions: tuple[str, ...]
    independent_review_record_digest: str

    created_at: str
    reviewed_at: str
    activated_at: str | None
    expires_at: str
    activation_record_digest: str | None
    ledger_sequence: int | None

    def __post_init__(self):
        for label, value in (
            ("reviewer registry id", self.reviewer_registry_id),
            ("adapter principal", self.authenticated_adapter_principal),
            ("provider principal", self.provider_principal),
            ("provider account scope", self.provider_account_scope),
            ("serving backend", self.serving_backend),
            ("endpoint class", self.endpoint_class),
            ("actual model id", self.actual_model_id),
            ("foundation model", self.foundation_model),
            ("foundation revision", self.foundation_revision),
            ("foundation lineage class", self.foundation_lineage_class),
            ("correlation group", self.correlation_group),
            ("hosted-copy relationship", self.hosted_copy_relationship),
            ("derivative relationship", self.derivative_relationship),
            ("actual egress destination", self.actual_egress_destination),
        ):
            _require_identifier(value, label)

        auth_methods = _identifier_tuple(
            self.allowed_authentication_methods,
            "allowed authentication methods",
        )
        requested_aliases = _identifier_tuple(
            self.requested_model_aliases,
            "requested model aliases",
        )
        if not isinstance(self.permitted_fallback_identities, tuple):
            raise ValueError("permitted fallback identities must be a tuple")
        for fallback in self.permitted_fallback_identities:
            if not isinstance(fallback, PermittedFallbackIdentityV1):
                raise ValueError("permitted fallback identity type is invalid")
        fallback_identities = tuple(
            sorted(self.permitted_fallback_identities, key=lambda item: item.sort_key)
        )
        if len(set(fallback_identities)) != len(fallback_identities):
            raise ValueError("permitted fallback identities contain duplicates")
        requalification = _identifier_tuple(
            self.requalification_conditions,
            "lineage requalification conditions",
        )
        reviewer_classes = _enum_tuple(
            self.eligible_reviewer_classes,
            ReviewerClass,
            "eligible reviewer classes",
        )
        risk_levels = _enum_tuple(
            self.eligible_risk_levels,
            RiskLevel,
            "eligible risk levels",
        )
        object.__setattr__(self, "allowed_authentication_methods", auth_methods)
        object.__setattr__(self, "requested_model_aliases", requested_aliases)
        object.__setattr__(
            self,
            "permitted_fallback_identities",
            fallback_identities,
        )
        object.__setattr__(self, "requalification_conditions", requalification)
        object.__setattr__(self, "eligible_reviewer_classes", reviewer_classes)
        object.__setattr__(self, "eligible_risk_levels", risk_levels)

        if not isinstance(self.identity_precision, IdentityPrecisionV1):
            raise ValueError("identity precision is invalid")
        if not isinstance(self.execution_locality, ExecutionLocality):
            raise ValueError("execution locality is invalid")
        if not isinstance(self.approval_state, LineageApprovalState):
            raise ValueError("lineage approval state is invalid")
        if not isinstance(self.activation_state, RegistryActivationStateV1):
            raise ValueError("lineage activation state is invalid")
        _require_bool(self.data_egress_permitted, "data egress permission")
        _validate_egress(
            data_egress_permitted=self.data_egress_permitted,
            actual_egress_destination=self.actual_egress_destination,
        )

        _require_sha256(self.lineage_evidence_digest, "lineage evidence digest")
        _require_sha256(
            self.independent_review_record_digest,
            "lineage independent review record digest",
        )
        _validate_activation(
            activation_state=self.activation_state,
            created_at=self.created_at,
            reviewed_at=self.reviewed_at,
            activated_at=self.activated_at,
            expires_at=self.expires_at,
            activation_record_digest=self.activation_record_digest,
            ledger_sequence=self.ledger_sequence,
            label="lineage entry",
        )

        own_identity_key = (
            self.provider_principal,
            self.provider_account_scope,
            self.serving_backend,
            self.endpoint_class,
            self.authenticated_adapter_principal,
            self.actual_model_id,
        )
        if any(
            fallback.sort_key == own_identity_key
            for fallback in fallback_identities
        ):
            raise ValueError("lineage fallback set contains the primary actual identity")

        if self.activation_state is RegistryActivationStateV1.ACTIVE:
            if self.approval_state is not LineageApprovalState.APPROVED:
                raise ValueError("active lineage entry is not approved")
            if self.identity_precision is not IdentityPrecisionV1.EXACT:
                raise ValueError("active lineage entry must have exact identity")
            if not _is_exact_model_id(self.actual_model_id):
                raise ValueError("active lineage actual model is an alias")
            for label, value in (
                ("active lineage foundation model", self.foundation_model),
                ("active lineage foundation revision", self.foundation_revision),
                ("active lineage foundation class", self.foundation_lineage_class),
            ):
                _require_exact_identity_component(value, label)

        if (
            self.activation_state is RegistryActivationStateV1.REVOKED
            and self.approval_state is LineageApprovalState.APPROVED
        ):
            raise ValueError("revoked lineage entry cannot remain approved")

    @property
    def exact_lookup_key(self) -> tuple[str, str, str]:
        return (
            self.provider_principal,
            self.serving_backend,
            self.actual_model_id,
        )

    @property
    def permitted_identity(self) -> PermittedFallbackIdentityV1:
        return PermittedFallbackIdentityV1(
            authenticated_adapter_principal=self.authenticated_adapter_principal,
            provider_principal=self.provider_principal,
            provider_account_scope=self.provider_account_scope,
            serving_backend=self.serving_backend,
            endpoint_class=self.endpoint_class,
            actual_model_id=self.actual_model_id,
        )

    def stable_mapping(self) -> dict:
        return {
            "reviewer_registry_id": self.reviewer_registry_id,
            "authenticated_adapter_principal": self.authenticated_adapter_principal,
            "allowed_authentication_methods": list(self.allowed_authentication_methods),
            "provider_principal": self.provider_principal,
            "provider_account_scope": self.provider_account_scope,
            "serving_backend": self.serving_backend,
            "endpoint_class": self.endpoint_class,
            "requested_model_aliases": list(self.requested_model_aliases),
            "actual_model_id": self.actual_model_id,
            "identity_precision": self.identity_precision.value,
            "permitted_fallback_identities": [
                identity.stable_mapping()
                for identity in self.permitted_fallback_identities
            ],
            "foundation_model": self.foundation_model,
            "foundation_revision": self.foundation_revision,
            "foundation_lineage_class": self.foundation_lineage_class,
            "correlation_group": self.correlation_group,
            "hosted_copy_relationship": self.hosted_copy_relationship,
            "derivative_relationship": self.derivative_relationship,
            "execution_locality": self.execution_locality.value,
            "data_egress_permitted": self.data_egress_permitted,
            "actual_egress_destination": self.actual_egress_destination,
            "eligible_reviewer_classes": [
                value.value for value in self.eligible_reviewer_classes
            ],
            "eligible_risk_levels": [
                value.value for value in self.eligible_risk_levels
            ],
            "lineage_evidence_digest": self.lineage_evidence_digest,
            "approval_state": self.approval_state.value,
            "activation_state": self.activation_state.value,
            "requalification_conditions": list(self.requalification_conditions),
            "independent_review_record_digest": (
                self.independent_review_record_digest
            ),
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "activated_at": self.activated_at,
            "expires_at": self.expires_at,
            "activation_record_digest": self.activation_record_digest,
            "ledger_sequence": self.ledger_sequence,
        }

    @property
    def entry_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    def to_mapping(self) -> dict:
        return self.stable_mapping() | {"entry_digest": self.entry_digest}

    @classmethod
    def from_mapping(cls, value: Mapping) -> "LineageIdentityEntryV1":
        raw = _require_mapping(value, _LINEAGE_ENTRY_KEYS, "lineage entry")
        entry = cls(
            reviewer_registry_id=_require_string(
                raw["reviewer_registry_id"], "reviewer registry id"
            ),
            authenticated_adapter_principal=_require_string(
                raw["authenticated_adapter_principal"], "adapter principal"
            ),
            allowed_authentication_methods=_tuple_from_list(
                raw["allowed_authentication_methods"],
                "allowed authentication methods",
            ),
            provider_principal=_require_string(
                raw["provider_principal"], "provider principal"
            ),
            provider_account_scope=_require_string(
                raw["provider_account_scope"], "provider account scope"
            ),
            serving_backend=_require_string(raw["serving_backend"], "serving backend"),
            endpoint_class=_require_string(raw["endpoint_class"], "endpoint class"),
            requested_model_aliases=_tuple_from_list(
                raw["requested_model_aliases"], "requested model aliases"
            ),
            actual_model_id=_require_string(raw["actual_model_id"], "actual model id"),
            identity_precision=_enum(
                raw["identity_precision"], IdentityPrecisionV1, "identity precision"
            ),
            permitted_fallback_identities=tuple(
                PermittedFallbackIdentityV1.from_mapping(item)
                for item in _tuple_from_list(
                    raw["permitted_fallback_identities"],
                    "permitted fallback identities",
                )
            ),
            foundation_model=_require_string(raw["foundation_model"], "foundation model"),
            foundation_revision=_require_string(
                raw["foundation_revision"], "foundation revision"
            ),
            foundation_lineage_class=_require_string(
                raw["foundation_lineage_class"], "foundation lineage class"
            ),
            correlation_group=_require_string(
                raw["correlation_group"], "correlation group"
            ),
            hosted_copy_relationship=_require_string(
                raw["hosted_copy_relationship"], "hosted-copy relationship"
            ),
            derivative_relationship=_require_string(
                raw["derivative_relationship"], "derivative relationship"
            ),
            execution_locality=_enum(
                raw["execution_locality"], ExecutionLocality, "execution locality"
            ),
            data_egress_permitted=_require_bool(
                raw["data_egress_permitted"], "data egress permission"
            ),
            actual_egress_destination=_require_string(
                raw["actual_egress_destination"], "actual egress destination"
            ),
            eligible_reviewer_classes=tuple(
                _enum(item, ReviewerClass, "eligible reviewer class")
                for item in _tuple_from_list(
                    raw["eligible_reviewer_classes"], "eligible reviewer classes"
                )
            ),
            eligible_risk_levels=tuple(
                _enum(item, RiskLevel, "eligible risk level")
                for item in _tuple_from_list(
                    raw["eligible_risk_levels"], "eligible risk levels"
                )
            ),
            lineage_evidence_digest=_require_string(
                raw["lineage_evidence_digest"], "lineage evidence digest"
            ),
            approval_state=_enum(
                raw["approval_state"], LineageApprovalState, "lineage approval state"
            ),
            activation_state=_enum(
                raw["activation_state"],
                RegistryActivationStateV1,
                "lineage activation state",
            ),
            requalification_conditions=_tuple_from_list(
                raw["requalification_conditions"],
                "lineage requalification conditions",
            ),
            independent_review_record_digest=_require_string(
                raw["independent_review_record_digest"],
                "lineage independent review record digest",
            ),
            created_at=_require_string(raw["created_at"], "lineage creation timestamp"),
            reviewed_at=_require_string(raw["reviewed_at"], "lineage review timestamp"),
            activated_at=(
                _require_string(raw["activated_at"], "lineage activation timestamp")
                if raw["activated_at"] is not None
                else None
            ),
            expires_at=_require_string(raw["expires_at"], "lineage expiry timestamp"),
            activation_record_digest=(
                _require_string(
                    raw["activation_record_digest"], "lineage activation record digest"
                )
                if raw["activation_record_digest"] is not None
                else None
            ),
            ledger_sequence=_require_optional_positive_u64(
                raw["ledger_sequence"], "lineage ledger sequence"
            ),
        )
        _require_sha256(raw["entry_digest"], "lineage entry digest")
        if raw["entry_digest"] != entry.entry_digest:
            raise ValueError("lineage entry digest mismatch")
        return entry

    def is_current_at(self, as_of: datetime) -> bool:
        as_of = _require_utc_datetime(as_of, "lineage evaluation timestamp")
        if (
            self.activation_state is not RegistryActivationStateV1.ACTIVE
            or self.approval_state is not LineageApprovalState.APPROVED
            or self.identity_precision is not IdentityPrecisionV1.EXACT
            or not _is_exact_model_id(self.actual_model_id)
            or self.activated_at is None
        ):
            return False
        activated = _parse_timestamp(self.activated_at, "lineage activation timestamp")
        expires = _parse_timestamp(self.expires_at, "lineage expiry timestamp")
        return activated <= as_of < expires

    def is_bootstrap_seed_current_at(self, as_of: datetime) -> bool:
        """Eligibility before normal activation is supplied by ledger genesis."""

        as_of = _require_utc_datetime(as_of, "lineage seed evaluation timestamp")
        if (
            self.activation_state is not RegistryActivationStateV1.PROPOSED
            or self.approval_state is not LineageApprovalState.APPROVED
            or self.identity_precision is not IdentityPrecisionV1.EXACT
            or not _is_exact_model_id(self.actual_model_id)
        ):
            return False
        for value in (
            self.foundation_model,
            self.foundation_revision,
            self.foundation_lineage_class,
        ):
            if not _is_exact_model_id(value):
                return False
        reviewed = _parse_timestamp(self.reviewed_at, "lineage review timestamp")
        expires = _parse_timestamp(self.expires_at, "lineage expiry timestamp")
        return reviewed <= as_of < expires


_LINEAGE_ENTRY_KEYS = {
    "reviewer_registry_id",
    "authenticated_adapter_principal",
    "allowed_authentication_methods",
    "provider_principal",
    "provider_account_scope",
    "serving_backend",
    "endpoint_class",
    "requested_model_aliases",
    "actual_model_id",
    "identity_precision",
    "permitted_fallback_identities",
    "foundation_model",
    "foundation_revision",
    "foundation_lineage_class",
    "correlation_group",
    "hosted_copy_relationship",
    "derivative_relationship",
    "execution_locality",
    "data_egress_permitted",
    "actual_egress_destination",
    "eligible_reviewer_classes",
    "eligible_risk_levels",
    "lineage_evidence_digest",
    "approval_state",
    "activation_state",
    "requalification_conditions",
    "independent_review_record_digest",
    "created_at",
    "reviewed_at",
    "activated_at",
    "expires_at",
    "activation_record_digest",
    "ledger_sequence",
    "entry_digest",
}


@dataclass(frozen=True)
class LineageRegistryV1:
    sequence_number: int
    previous_snapshot_digest: str
    policy_revision: str
    entries: tuple[LineageIdentityEntryV1, ...]

    activation_state: RegistryActivationStateV1
    independent_review_record_digest: str
    created_at: str
    reviewed_at: str
    activated_at: str | None
    expires_at: str
    activation_record_digest: str | None
    ledger_sequence: int | None

    registry_version: str = LINEAGE_REGISTRY_VERSION

    def __post_init__(self):
        if self.registry_version != LINEAGE_REGISTRY_VERSION:
            raise ValueError("lineage registry version mismatch")
        _require_positive_u64(self.sequence_number, "lineage registry sequence")
        _require_sha256(
            self.previous_snapshot_digest,
            "previous lineage registry snapshot digest",
        )
        if self.sequence_number == 1:
            if self.previous_snapshot_digest != GENESIS_SNAPSHOT_DIGEST:
                raise ValueError("lineage registry genesis predecessor mismatch")
        elif self.previous_snapshot_digest == GENESIS_SNAPSHOT_DIGEST:
            raise ValueError("non-genesis lineage registry lacks predecessor")

        _require_identifier(self.policy_revision, "lineage policy revision")
        if not isinstance(self.entries, tuple):
            raise ValueError("lineage registry entries must be a tuple")
        for entry in self.entries:
            if not isinstance(entry, LineageIdentityEntryV1):
                raise ValueError("lineage registry entry type is invalid")
        if not isinstance(self.activation_state, RegistryActivationStateV1):
            raise ValueError("lineage registry activation state is invalid")
        _require_sha256(
            self.independent_review_record_digest,
            "lineage registry independent review record digest",
        )
        _validate_activation(
            activation_state=self.activation_state,
            created_at=self.created_at,
            reviewed_at=self.reviewed_at,
            activated_at=self.activated_at,
            expires_at=self.expires_at,
            activation_record_digest=self.activation_record_digest,
            ledger_sequence=self.ledger_sequence,
            label="lineage registry",
        )

        if self.activation_state is RegistryActivationStateV1.ACTIVE and not self.entries:
            raise ValueError("active lineage registry must not be empty")

        ordered = tuple(sorted(self.entries, key=lambda entry: entry.reviewer_registry_id))
        object.__setattr__(self, "entries", ordered)
        seen_ids: set[str] = set()
        seen_exact_keys: set[tuple[str, str, str]] = set()
        for entry in ordered:
            if entry.reviewer_registry_id in seen_ids:
                raise ValueError("duplicate lineage reviewer registry id")
            if entry.exact_lookup_key in seen_exact_keys:
                raise ValueError("duplicate lineage exact execution identity")
            seen_ids.add(entry.reviewer_registry_id)
            seen_exact_keys.add(entry.exact_lookup_key)

    def stable_mapping(self) -> dict:
        return {
            "registry_version": self.registry_version,
            "sequence_number": self.sequence_number,
            "previous_snapshot_digest": self.previous_snapshot_digest,
            "policy_revision": self.policy_revision,
            "entries": [entry.to_mapping() for entry in self.entries],
            "activation_state": self.activation_state.value,
            "independent_review_record_digest": self.independent_review_record_digest,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "activated_at": self.activated_at,
            "expires_at": self.expires_at,
            "activation_record_digest": self.activation_record_digest,
            "ledger_sequence": self.ledger_sequence,
        }

    @property
    def snapshot_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    def to_mapping(self) -> dict:
        return self.stable_mapping() | {"snapshot_digest": self.snapshot_digest}

    @classmethod
    def from_mapping(cls, value: Mapping) -> "LineageRegistryV1":
        raw = _require_mapping(value, _LINEAGE_REGISTRY_KEYS, "lineage registry")
        entries_raw = raw["entries"]
        if not isinstance(entries_raw, list):
            raise ValueError("lineage registry entries must be an array")
        registry = cls(
            registry_version=_require_string(
                raw["registry_version"], "lineage registry version"
            ),
            sequence_number=_require_positive_u64(
                raw["sequence_number"], "lineage registry sequence"
            ),
            previous_snapshot_digest=_require_string(
                raw["previous_snapshot_digest"],
                "previous lineage registry snapshot digest",
            ),
            policy_revision=_require_string(
                raw["policy_revision"], "lineage policy revision"
            ),
            entries=tuple(LineageIdentityEntryV1.from_mapping(item) for item in entries_raw),
            activation_state=_enum(
                raw["activation_state"],
                RegistryActivationStateV1,
                "lineage registry activation state",
            ),
            independent_review_record_digest=_require_string(
                raw["independent_review_record_digest"],
                "lineage registry independent review record digest",
            ),
            created_at=_require_string(raw["created_at"], "lineage registry creation"),
            reviewed_at=_require_string(raw["reviewed_at"], "lineage registry review"),
            activated_at=(
                _require_string(raw["activated_at"], "lineage registry activation")
                if raw["activated_at"] is not None
                else None
            ),
            expires_at=_require_string(raw["expires_at"], "lineage registry expiry"),
            activation_record_digest=(
                _require_string(
                    raw["activation_record_digest"],
                    "lineage registry activation record digest",
                )
                if raw["activation_record_digest"] is not None
                else None
            ),
            ledger_sequence=_require_optional_positive_u64(
                raw["ledger_sequence"], "lineage registry ledger sequence"
            ),
        )
        _require_sha256(raw["snapshot_digest"], "lineage registry snapshot digest")
        if raw["snapshot_digest"] != registry.snapshot_digest:
            raise ValueError("lineage registry snapshot digest mismatch")
        return registry

    def validate_successor(self, previous: "LineageRegistryV1") -> None:
        if not isinstance(previous, LineageRegistryV1):
            raise ValueError("lineage predecessor type is invalid")
        if self.sequence_number != previous.sequence_number + 1:
            raise ValueError("lineage registry sequence gap or reorder")
        if self.previous_snapshot_digest != previous.snapshot_digest:
            raise ValueError("lineage registry predecessor digest mismatch")
        if _parse_timestamp(self.created_at, "lineage registry creation") < _parse_timestamp(
            previous.created_at,
            "previous lineage registry creation",
        ):
            raise ValueError("lineage registry successor predates predecessor")

    def _assert_current(
        self,
        *,
        current_snapshot_digest: str,
        as_of: str,
        bootstrap_complete_payload: BootstrapCompletePayloadV1 | None = None,
    ) -> tuple[datetime, bool]:
        _require_sha256(current_snapshot_digest, "current lineage snapshot digest")
        instant = _parse_timestamp(as_of, "lineage registry current timestamp")
        if current_snapshot_digest != self.snapshot_digest:
            raise ValueError("lineage registry is stale")
        if self.activation_state is RegistryActivationStateV1.ACTIVE:
            if self.activated_at is None:
                raise ValueError("lineage registry is not active")
            activated = _parse_timestamp(self.activated_at, "lineage registry activation")
            expires = _parse_timestamp(self.expires_at, "lineage registry expiry")
            if not activated <= instant < expires:
                raise ValueError("lineage registry is not current")
            return instant, False

        seed_activated = (
            self.activation_state is RegistryActivationStateV1.PROPOSED
            and isinstance(bootstrap_complete_payload, BootstrapCompletePayloadV1)
            and bootstrap_complete_payload.lineage_registry_snapshot_digest
            == self.snapshot_digest
        )
        if not seed_activated:
            raise ValueError("lineage registry is not active")
        completed = _parse_timestamp(
            bootstrap_complete_payload.completed_at,
            "bootstrap completion timestamp",
        )
        expires = _parse_timestamp(self.expires_at, "lineage registry expiry")
        if not completed <= instant < expires:
            raise ValueError("bootstrap-seed lineage registry is not current")
        return instant, True

    def require_current_entry(
        self,
        *,
        reviewer_registry_id: str,
        current_snapshot_digest: str,
        as_of: str,
        bootstrap_complete_payload: BootstrapCompletePayloadV1 | None = None,
    ) -> LineageIdentityEntryV1:
        reviewer_registry_id = _require_identifier(
            reviewer_registry_id,
            "reviewer registry id",
        )
        instant, bootstrap_seed = self._assert_current(
            current_snapshot_digest=current_snapshot_digest,
            as_of=as_of,
            bootstrap_complete_payload=bootstrap_complete_payload,
        )
        matches = [
            entry for entry in self.entries
            if entry.reviewer_registry_id == reviewer_registry_id
        ]
        is_current = (
            matches[0].is_bootstrap_seed_current_at(instant)
            if len(matches) == 1 and bootstrap_seed
            else len(matches) == 1 and matches[0].is_current_at(instant)
        )
        if len(matches) != 1 or not is_current:
            raise ValueError("lineage identity is not uniquely active and current")
        return matches[0]

    def compile_g0a_snapshot(
        self,
        *,
        current_snapshot_digest: str,
        as_of: str,
        bootstrap_complete_payload: BootstrapCompletePayloadV1 | None = None,
    ) -> LineageRegistrySnapshotV1:
        instant, bootstrap_seed = self._assert_current(
            current_snapshot_digest=current_snapshot_digest,
            as_of=as_of,
            bootstrap_complete_payload=bootstrap_complete_payload,
        )
        active = [
            entry for entry in self.entries
            if (
                entry.is_bootstrap_seed_current_at(instant)
                if bootstrap_seed
                else entry.is_current_at(instant)
            )
        ]
        if not active:
            raise ValueError("lineage registry has no active current exact identities")
        return LineageRegistrySnapshotV1(
            policy_revision=self.policy_revision,
            entries=tuple(
                LineageRegistryEntryV1(
                    provider_principal=entry.provider_principal,
                    serving_backend=entry.serving_backend,
                    actual_model_id=entry.actual_model_id,
                    foundation_model=entry.foundation_model,
                    foundation_revision=entry.foundation_revision,
                    foundation_lineage_class=entry.foundation_lineage_class,
                    correlation_group=entry.correlation_group,
                    approval_state=LineageApprovalState.APPROVED,
                )
                for entry in active
            ),
            source_registry_snapshot_digest=self.snapshot_digest,
        )


_LINEAGE_REGISTRY_KEYS = {
    "registry_version",
    "sequence_number",
    "previous_snapshot_digest",
    "policy_revision",
    "entries",
    "activation_state",
    "independent_review_record_digest",
    "created_at",
    "reviewed_at",
    "activated_at",
    "expires_at",
    "activation_record_digest",
    "ledger_sequence",
    "snapshot_digest",
}


@dataclass(frozen=True)
class QualificationEntryV1:
    qualification_registry_id: str
    reviewer_registry_id: str
    requested_reviewer_registry_id: str

    authenticated_adapter_principal: str
    authentication_method: str
    provider_principal: str
    provider_account_scope: str
    serving_backend: str
    endpoint_class: str

    requested_model_id: str
    actual_model_id: str
    identity_precision: IdentityPrecisionV1
    fallback_state: FallbackStateV1

    foundation_model: str
    foundation_revision: str
    foundation_lineage_class: str
    execution_locality: ExecutionLocality
    data_egress_permitted: bool
    actual_egress_destination: str

    identity_envelope_digest: str
    protocol_revision: str
    benchmark_harness_policy_revision: str
    benchmark_version: str
    custody_version: str
    harness_revision: str
    harness_digest: str
    scoring_revision: str
    scoring_digest: str
    public_fixture_manifest_digest: str
    sealed_fixture_manifest_digest: str
    sealed_label_manifest_digest: str
    lineage_registry_snapshot_digest: str
    qualification_evidence_digest: str
    privacy_mode: str
    egress_decision_digest: str

    status: QualificationStatusV1
    qualified_reviewer_class: ReviewerClass
    eligible_risk_levels: tuple[RiskLevel, ...]

    activation_state: RegistryActivationStateV1
    requalification_conditions: tuple[str, ...]
    independent_review_record_digest: str
    created_at: str
    reviewed_at: str
    activated_at: str | None
    expires_at: str
    activation_record_digest: str | None
    ledger_sequence: int | None

    def __post_init__(self):
        for label, value in (
            ("qualification registry id", self.qualification_registry_id),
            ("reviewer registry id", self.reviewer_registry_id),
            ("requested reviewer registry id", self.requested_reviewer_registry_id),
            ("qualification adapter principal", self.authenticated_adapter_principal),
            ("qualification authentication method", self.authentication_method),
            ("qualification provider principal", self.provider_principal),
            ("qualification provider account scope", self.provider_account_scope),
            ("qualification serving backend", self.serving_backend),
            ("qualification endpoint class", self.endpoint_class),
            ("qualification requested model id", self.requested_model_id),
            ("qualification actual model id", self.actual_model_id),
            ("qualification foundation model", self.foundation_model),
            ("qualification foundation revision", self.foundation_revision),
            ("qualification foundation lineage class", self.foundation_lineage_class),
            ("qualification egress destination", self.actual_egress_destination),
            ("qualification protocol revision", self.protocol_revision),
            (
                "qualification benchmark/harness policy revision",
                self.benchmark_harness_policy_revision,
            ),
            ("qualification benchmark version", self.benchmark_version),
            ("qualification custody version", self.custody_version),
            ("qualification harness revision", self.harness_revision),
            ("qualification scoring revision", self.scoring_revision),
            ("qualification privacy mode", self.privacy_mode),
        ):
            _require_identifier(value, label)

        for label, value in (
            ("identity envelope digest", self.identity_envelope_digest),
            ("harness digest", self.harness_digest),
            ("scoring digest", self.scoring_digest),
            ("public fixture manifest digest", self.public_fixture_manifest_digest),
            ("sealed fixture manifest digest", self.sealed_fixture_manifest_digest),
            ("sealed label manifest digest", self.sealed_label_manifest_digest),
            ("lineage registry snapshot digest", self.lineage_registry_snapshot_digest),
            ("qualification evidence digest", self.qualification_evidence_digest),
            ("egress decision digest", self.egress_decision_digest),
            (
                "qualification independent review record digest",
                self.independent_review_record_digest,
            ),
        ):
            _require_sha256(value, label)

        if not isinstance(self.identity_precision, IdentityPrecisionV1):
            raise ValueError("qualification identity precision is invalid")
        if not isinstance(self.fallback_state, FallbackStateV1):
            raise ValueError("qualification fallback state is invalid")
        if not isinstance(self.execution_locality, ExecutionLocality):
            raise ValueError("qualification execution locality is invalid")
        if not isinstance(self.status, QualificationStatusV1):
            raise ValueError("qualification status is invalid")
        if not isinstance(self.qualified_reviewer_class, ReviewerClass):
            raise ValueError("qualified reviewer class is invalid")
        if not isinstance(self.activation_state, RegistryActivationStateV1):
            raise ValueError("qualification activation state is invalid")
        _require_bool(self.data_egress_permitted, "qualification data egress permission")
        _validate_egress(
            data_egress_permitted=self.data_egress_permitted,
            actual_egress_destination=self.actual_egress_destination,
        )

        risks = _enum_tuple(
            self.eligible_risk_levels,
            RiskLevel,
            "qualification eligible risk levels",
        )
        conditions = _identifier_tuple(
            self.requalification_conditions,
            "qualification requalification conditions",
        )
        object.__setattr__(self, "eligible_risk_levels", risks)
        object.__setattr__(self, "requalification_conditions", conditions)

        _validate_activation(
            activation_state=self.activation_state,
            created_at=self.created_at,
            reviewed_at=self.reviewed_at,
            activated_at=self.activated_at,
            expires_at=self.expires_at,
            activation_record_digest=self.activation_record_digest,
            ledger_sequence=self.ledger_sequence,
            label="qualification entry",
        )

        if self.fallback_state is FallbackStateV1.NO_FALLBACK:
            if (
                self.requested_model_id != self.actual_model_id
                or self.requested_reviewer_registry_id != self.reviewer_registry_id
            ):
                raise ValueError("no-fallback qualification identity is inconsistent")
        else:
            if (
                self.requested_model_id == self.actual_model_id
                or self.requested_reviewer_registry_id == self.reviewer_registry_id
            ):
                raise ValueError("fallback qualification must bind distinct identities")

        expected_class = _QUALIFIED_STATUS_CLASS.get(self.status)
        if expected_class is not None and expected_class is not self.qualified_reviewer_class:
            raise ValueError("qualification status/reviewer class mismatch")

        if self.activation_state is RegistryActivationStateV1.ACTIVE:
            if expected_class is None:
                raise ValueError("non-qualified status cannot be active")
            if self.identity_precision is not IdentityPrecisionV1.EXACT:
                raise ValueError("active qualification identity must be exact")
            if not _is_exact_model_id(self.actual_model_id):
                raise ValueError("active qualification actual model is an alias")
            for label, value in (
                ("active qualification foundation model", self.foundation_model),
                ("active qualification foundation revision", self.foundation_revision),
                (
                    "active qualification foundation class",
                    self.foundation_lineage_class,
                ),
            ):
                _require_exact_identity_component(value, label)
            if self.fallback_state not in {
                FallbackStateV1.NO_FALLBACK,
                FallbackStateV1.POLICY_PERMITTED_FALLBACK,
            }:
                raise ValueError("unverified fallback cannot be actively qualified")

        if self.privacy_mode not in {"PUBLIC", "RESTRICTED", "PRIVATE"}:
            raise ValueError("qualification privacy mode is invalid")
        if self.privacy_mode == "PRIVATE" and self.data_egress_permitted:
            raise ValueError("PRIVATE qualification material cannot permit data egress")

        if self.status is QualificationStatusV1.REVOKED:
            if self.activation_state is not RegistryActivationStateV1.REVOKED:
                raise ValueError("revoked qualification lacks revoked activation state")
        elif self.activation_state is RegistryActivationStateV1.REVOKED:
            raise ValueError("revoked activation state requires revoked qualification")

    def stable_mapping(self) -> dict:
        return {
            "qualification_registry_id": self.qualification_registry_id,
            "reviewer_registry_id": self.reviewer_registry_id,
            "requested_reviewer_registry_id": self.requested_reviewer_registry_id,
            "authenticated_adapter_principal": self.authenticated_adapter_principal,
            "authentication_method": self.authentication_method,
            "provider_principal": self.provider_principal,
            "provider_account_scope": self.provider_account_scope,
            "serving_backend": self.serving_backend,
            "endpoint_class": self.endpoint_class,
            "requested_model_id": self.requested_model_id,
            "actual_model_id": self.actual_model_id,
            "identity_precision": self.identity_precision.value,
            "fallback_state": self.fallback_state.value,
            "foundation_model": self.foundation_model,
            "foundation_revision": self.foundation_revision,
            "foundation_lineage_class": self.foundation_lineage_class,
            "execution_locality": self.execution_locality.value,
            "data_egress_permitted": self.data_egress_permitted,
            "actual_egress_destination": self.actual_egress_destination,
            "identity_envelope_digest": self.identity_envelope_digest,
            "protocol_revision": self.protocol_revision,
            "benchmark_harness_policy_revision": (
                self.benchmark_harness_policy_revision
            ),
            "benchmark_version": self.benchmark_version,
            "custody_version": self.custody_version,
            "harness_revision": self.harness_revision,
            "harness_digest": self.harness_digest,
            "scoring_revision": self.scoring_revision,
            "scoring_digest": self.scoring_digest,
            "public_fixture_manifest_digest": self.public_fixture_manifest_digest,
            "sealed_fixture_manifest_digest": self.sealed_fixture_manifest_digest,
            "sealed_label_manifest_digest": self.sealed_label_manifest_digest,
            "lineage_registry_snapshot_digest": self.lineage_registry_snapshot_digest,
            "qualification_evidence_digest": self.qualification_evidence_digest,
            "privacy_mode": self.privacy_mode,
            "egress_decision_digest": self.egress_decision_digest,
            "status": self.status.value,
            "qualified_reviewer_class": self.qualified_reviewer_class.value,
            "eligible_risk_levels": [value.value for value in self.eligible_risk_levels],
            "activation_state": self.activation_state.value,
            "requalification_conditions": list(self.requalification_conditions),
            "independent_review_record_digest": (
                self.independent_review_record_digest
            ),
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "activated_at": self.activated_at,
            "expires_at": self.expires_at,
            "activation_record_digest": self.activation_record_digest,
            "ledger_sequence": self.ledger_sequence,
        }

    @property
    def entry_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    def to_mapping(self) -> dict:
        return self.stable_mapping() | {"entry_digest": self.entry_digest}

    @classmethod
    def from_mapping(cls, value: Mapping) -> "QualificationEntryV1":
        raw = _require_mapping(value, _QUALIFICATION_ENTRY_KEYS, "qualification entry")
        entry = cls(
            qualification_registry_id=_require_string(
                raw["qualification_registry_id"], "qualification registry id"
            ),
            reviewer_registry_id=_require_string(
                raw["reviewer_registry_id"], "reviewer registry id"
            ),
            requested_reviewer_registry_id=_require_string(
                raw["requested_reviewer_registry_id"],
                "requested reviewer registry id",
            ),
            authenticated_adapter_principal=_require_string(
                raw["authenticated_adapter_principal"], "qualification adapter principal"
            ),
            authentication_method=_require_string(
                raw["authentication_method"], "qualification authentication method"
            ),
            provider_principal=_require_string(
                raw["provider_principal"], "qualification provider principal"
            ),
            provider_account_scope=_require_string(
                raw["provider_account_scope"], "qualification provider account scope"
            ),
            serving_backend=_require_string(
                raw["serving_backend"], "qualification serving backend"
            ),
            endpoint_class=_require_string(
                raw["endpoint_class"], "qualification endpoint class"
            ),
            requested_model_id=_require_string(
                raw["requested_model_id"], "qualification requested model id"
            ),
            actual_model_id=_require_string(
                raw["actual_model_id"], "qualification actual model id"
            ),
            identity_precision=_enum(
                raw["identity_precision"], IdentityPrecisionV1, "identity precision"
            ),
            fallback_state=_enum(
                raw["fallback_state"], FallbackStateV1, "qualification fallback state"
            ),
            foundation_model=_require_string(
                raw["foundation_model"], "qualification foundation model"
            ),
            foundation_revision=_require_string(
                raw["foundation_revision"], "qualification foundation revision"
            ),
            foundation_lineage_class=_require_string(
                raw["foundation_lineage_class"],
                "qualification foundation lineage class",
            ),
            execution_locality=_enum(
                raw["execution_locality"],
                ExecutionLocality,
                "qualification execution locality",
            ),
            data_egress_permitted=_require_bool(
                raw["data_egress_permitted"], "qualification data egress permission"
            ),
            actual_egress_destination=_require_string(
                raw["actual_egress_destination"],
                "qualification actual egress destination",
            ),
            identity_envelope_digest=_require_string(
                raw["identity_envelope_digest"], "identity envelope digest"
            ),
            protocol_revision=_require_string(
                raw["protocol_revision"], "qualification protocol revision"
            ),
            benchmark_harness_policy_revision=_require_string(
                raw["benchmark_harness_policy_revision"],
                "qualification benchmark/harness policy revision",
            ),
            benchmark_version=_require_string(
                raw["benchmark_version"], "qualification benchmark version"
            ),
            custody_version=_require_string(
                raw["custody_version"], "qualification custody version"
            ),
            harness_revision=_require_string(
                raw["harness_revision"], "qualification harness revision"
            ),
            harness_digest=_require_string(raw["harness_digest"], "harness digest"),
            scoring_revision=_require_string(
                raw["scoring_revision"], "qualification scoring revision"
            ),
            scoring_digest=_require_string(raw["scoring_digest"], "scoring digest"),
            public_fixture_manifest_digest=_require_string(
                raw["public_fixture_manifest_digest"],
                "public fixture manifest digest",
            ),
            sealed_fixture_manifest_digest=_require_string(
                raw["sealed_fixture_manifest_digest"],
                "sealed fixture manifest digest",
            ),
            sealed_label_manifest_digest=_require_string(
                raw["sealed_label_manifest_digest"],
                "sealed label manifest digest",
            ),
            lineage_registry_snapshot_digest=_require_string(
                raw["lineage_registry_snapshot_digest"],
                "lineage registry snapshot digest",
            ),
            qualification_evidence_digest=_require_string(
                raw["qualification_evidence_digest"],
                "qualification evidence digest",
            ),
            privacy_mode=_require_string(raw["privacy_mode"], "qualification privacy mode"),
            egress_decision_digest=_require_string(
                raw["egress_decision_digest"], "egress decision digest"
            ),
            status=_enum(
                raw["status"], QualificationStatusV1, "qualification status"
            ),
            qualified_reviewer_class=_enum(
                raw["qualified_reviewer_class"],
                ReviewerClass,
                "qualified reviewer class",
            ),
            eligible_risk_levels=tuple(
                _enum(item, RiskLevel, "qualification eligible risk level")
                for item in _tuple_from_list(
                    raw["eligible_risk_levels"],
                    "qualification eligible risk levels",
                )
            ),
            activation_state=_enum(
                raw["activation_state"],
                RegistryActivationStateV1,
                "qualification activation state",
            ),
            requalification_conditions=_tuple_from_list(
                raw["requalification_conditions"],
                "qualification requalification conditions",
            ),
            independent_review_record_digest=_require_string(
                raw["independent_review_record_digest"],
                "qualification independent review record digest",
            ),
            created_at=_require_string(raw["created_at"], "qualification creation"),
            reviewed_at=_require_string(raw["reviewed_at"], "qualification review"),
            activated_at=(
                _require_string(raw["activated_at"], "qualification activation")
                if raw["activated_at"] is not None
                else None
            ),
            expires_at=_require_string(raw["expires_at"], "qualification expiry"),
            activation_record_digest=(
                _require_string(
                    raw["activation_record_digest"],
                    "qualification activation record digest",
                )
                if raw["activation_record_digest"] is not None
                else None
            ),
            ledger_sequence=_require_optional_positive_u64(
                raw["ledger_sequence"], "qualification ledger sequence"
            ),
        )
        _require_sha256(raw["entry_digest"], "qualification entry digest")
        if raw["entry_digest"] != entry.entry_digest:
            raise ValueError("qualification entry digest mismatch")
        return entry

    def is_current_at(self, as_of: datetime) -> bool:
        as_of = _require_utc_datetime(as_of, "qualification evaluation timestamp")
        if (
            self.activation_state is not RegistryActivationStateV1.ACTIVE
            or self.status not in _QUALIFIED_STATUS_CLASS
            or self.identity_precision is not IdentityPrecisionV1.EXACT
            or not _is_exact_model_id(self.actual_model_id)
            or self.fallback_state not in {
                FallbackStateV1.NO_FALLBACK,
                FallbackStateV1.POLICY_PERMITTED_FALLBACK,
            }
            or self.activated_at is None
        ):
            return False
        activated = _parse_timestamp(
            self.activated_at,
            "qualification activation timestamp",
        )
        expires = _parse_timestamp(self.expires_at, "qualification expiry timestamp")
        return activated <= as_of < expires

    def is_bootstrap_seed_current_at(self, as_of: datetime) -> bool:
        as_of = _require_utc_datetime(as_of, "qualification seed evaluation timestamp")
        if (
            self.activation_state is not RegistryActivationStateV1.PROPOSED
            or self.status not in _QUALIFIED_STATUS_CLASS
            or self.identity_precision is not IdentityPrecisionV1.EXACT
            or not _is_exact_model_id(self.actual_model_id)
            or self.fallback_state not in {
                FallbackStateV1.NO_FALLBACK,
                FallbackStateV1.POLICY_PERMITTED_FALLBACK,
            }
        ):
            return False
        for value in (
            self.foundation_model,
            self.foundation_revision,
            self.foundation_lineage_class,
        ):
            if not _is_exact_model_id(value):
                return False
        reviewed = _parse_timestamp(self.reviewed_at, "qualification review timestamp")
        expires = _parse_timestamp(self.expires_at, "qualification expiry timestamp")
        return reviewed <= as_of < expires


_QUALIFICATION_ENTRY_KEYS = {
    "qualification_registry_id",
    "reviewer_registry_id",
    "requested_reviewer_registry_id",
    "authenticated_adapter_principal",
    "authentication_method",
    "provider_principal",
    "provider_account_scope",
    "serving_backend",
    "endpoint_class",
    "requested_model_id",
    "actual_model_id",
    "identity_precision",
    "fallback_state",
    "foundation_model",
    "foundation_revision",
    "foundation_lineage_class",
    "execution_locality",
    "data_egress_permitted",
    "actual_egress_destination",
    "identity_envelope_digest",
    "protocol_revision",
    "benchmark_harness_policy_revision",
    "benchmark_version",
    "custody_version",
    "harness_revision",
    "harness_digest",
    "scoring_revision",
    "scoring_digest",
    "public_fixture_manifest_digest",
    "sealed_fixture_manifest_digest",
    "sealed_label_manifest_digest",
    "lineage_registry_snapshot_digest",
    "qualification_evidence_digest",
    "privacy_mode",
    "egress_decision_digest",
    "status",
    "qualified_reviewer_class",
    "eligible_risk_levels",
    "activation_state",
    "requalification_conditions",
    "independent_review_record_digest",
    "created_at",
    "reviewed_at",
    "activated_at",
    "expires_at",
    "activation_record_digest",
    "ledger_sequence",
    "entry_digest",
}


@dataclass(frozen=True)
class QualificationRegistryV1:
    sequence_number: int
    previous_snapshot_digest: str
    policy_revision: str
    entries: tuple[QualificationEntryV1, ...]

    activation_state: RegistryActivationStateV1
    independent_review_record_digest: str
    created_at: str
    reviewed_at: str
    activated_at: str | None
    expires_at: str
    activation_record_digest: str | None
    ledger_sequence: int | None

    registry_version: str = QUALIFICATION_REGISTRY_VERSION

    def __post_init__(self):
        if self.registry_version != QUALIFICATION_REGISTRY_VERSION:
            raise ValueError("qualification registry version mismatch")
        _require_positive_u64(self.sequence_number, "qualification registry sequence")
        _require_sha256(
            self.previous_snapshot_digest,
            "previous qualification registry snapshot digest",
        )
        if self.sequence_number == 1:
            if self.previous_snapshot_digest != GENESIS_SNAPSHOT_DIGEST:
                raise ValueError("qualification registry genesis predecessor mismatch")
        elif self.previous_snapshot_digest == GENESIS_SNAPSHOT_DIGEST:
            raise ValueError("non-genesis qualification registry lacks predecessor")

        _require_identifier(self.policy_revision, "qualification policy revision")
        if not isinstance(self.entries, tuple):
            raise ValueError("qualification registry entries must be a tuple")
        for entry in self.entries:
            if not isinstance(entry, QualificationEntryV1):
                raise ValueError("qualification registry entry type is invalid")
        if not isinstance(self.activation_state, RegistryActivationStateV1):
            raise ValueError("qualification registry activation state is invalid")
        _require_sha256(
            self.independent_review_record_digest,
            "qualification registry independent review record digest",
        )
        _validate_activation(
            activation_state=self.activation_state,
            created_at=self.created_at,
            reviewed_at=self.reviewed_at,
            activated_at=self.activated_at,
            expires_at=self.expires_at,
            activation_record_digest=self.activation_record_digest,
            ledger_sequence=self.ledger_sequence,
            label="qualification registry",
        )

        if self.activation_state is RegistryActivationStateV1.ACTIVE and not self.entries:
            raise ValueError("active qualification registry must not be empty")

        ordered = tuple(
            sorted(self.entries, key=lambda entry: entry.qualification_registry_id)
        )
        object.__setattr__(self, "entries", ordered)
        seen_ids: set[str] = set()
        seen_reviewer_ids: set[str] = set()
        for entry in ordered:
            if entry.qualification_registry_id in seen_ids:
                raise ValueError("duplicate qualification registry id")
            if entry.reviewer_registry_id in seen_reviewer_ids:
                raise ValueError("duplicate qualification for reviewer registry identity")
            seen_ids.add(entry.qualification_registry_id)
            seen_reviewer_ids.add(entry.reviewer_registry_id)

    def stable_mapping(self) -> dict:
        return {
            "registry_version": self.registry_version,
            "sequence_number": self.sequence_number,
            "previous_snapshot_digest": self.previous_snapshot_digest,
            "policy_revision": self.policy_revision,
            "entries": [entry.to_mapping() for entry in self.entries],
            "activation_state": self.activation_state.value,
            "independent_review_record_digest": self.independent_review_record_digest,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "activated_at": self.activated_at,
            "expires_at": self.expires_at,
            "activation_record_digest": self.activation_record_digest,
            "ledger_sequence": self.ledger_sequence,
        }

    @property
    def snapshot_digest(self) -> str:
        return canonical_digest(self.stable_mapping())

    def to_mapping(self) -> dict:
        return self.stable_mapping() | {"snapshot_digest": self.snapshot_digest}

    @classmethod
    def from_mapping(cls, value: Mapping) -> "QualificationRegistryV1":
        raw = _require_mapping(
            value,
            _QUALIFICATION_REGISTRY_KEYS,
            "qualification registry",
        )
        entries_raw = raw["entries"]
        if not isinstance(entries_raw, list):
            raise ValueError("qualification registry entries must be an array")
        registry = cls(
            registry_version=_require_string(
                raw["registry_version"], "qualification registry version"
            ),
            sequence_number=_require_positive_u64(
                raw["sequence_number"], "qualification registry sequence"
            ),
            previous_snapshot_digest=_require_string(
                raw["previous_snapshot_digest"],
                "previous qualification registry snapshot digest",
            ),
            policy_revision=_require_string(
                raw["policy_revision"], "qualification policy revision"
            ),
            entries=tuple(QualificationEntryV1.from_mapping(item) for item in entries_raw),
            activation_state=_enum(
                raw["activation_state"],
                RegistryActivationStateV1,
                "qualification registry activation state",
            ),
            independent_review_record_digest=_require_string(
                raw["independent_review_record_digest"],
                "qualification registry independent review record digest",
            ),
            created_at=_require_string(raw["created_at"], "qualification registry creation"),
            reviewed_at=_require_string(raw["reviewed_at"], "qualification registry review"),
            activated_at=(
                _require_string(raw["activated_at"], "qualification registry activation")
                if raw["activated_at"] is not None
                else None
            ),
            expires_at=_require_string(raw["expires_at"], "qualification registry expiry"),
            activation_record_digest=(
                _require_string(
                    raw["activation_record_digest"],
                    "qualification registry activation record digest",
                )
                if raw["activation_record_digest"] is not None
                else None
            ),
            ledger_sequence=_require_optional_positive_u64(
                raw["ledger_sequence"], "qualification registry ledger sequence"
            ),
        )
        _require_sha256(raw["snapshot_digest"], "qualification registry snapshot digest")
        if raw["snapshot_digest"] != registry.snapshot_digest:
            raise ValueError("qualification registry snapshot digest mismatch")
        return registry

    def validate_successor(self, previous: "QualificationRegistryV1") -> None:
        if not isinstance(previous, QualificationRegistryV1):
            raise ValueError("qualification predecessor type is invalid")
        if self.sequence_number != previous.sequence_number + 1:
            raise ValueError("qualification registry sequence gap or reorder")
        if self.previous_snapshot_digest != previous.snapshot_digest:
            raise ValueError("qualification registry predecessor digest mismatch")
        if _parse_timestamp(
            self.created_at,
            "qualification registry creation",
        ) < _parse_timestamp(
            previous.created_at,
            "previous qualification registry creation",
        ):
            raise ValueError("qualification registry successor predates predecessor")

    def _assert_current(
        self,
        *,
        current_snapshot_digest: str,
        as_of: str,
        bootstrap_complete_payload: BootstrapCompletePayloadV1 | None = None,
    ) -> tuple[datetime, bool]:
        _require_sha256(current_snapshot_digest, "current qualification snapshot digest")
        instant = _parse_timestamp(as_of, "qualification registry current timestamp")
        if current_snapshot_digest != self.snapshot_digest:
            raise ValueError("qualification registry is stale")
        if self.activation_state is RegistryActivationStateV1.ACTIVE:
            if self.activated_at is None:
                raise ValueError("qualification registry is not active")
            activated = _parse_timestamp(
                self.activated_at,
                "qualification registry activation",
            )
            expires = _parse_timestamp(self.expires_at, "qualification registry expiry")
            if not activated <= instant < expires:
                raise ValueError("qualification registry is not current")
            return instant, False

        seed_activated = (
            self.activation_state is RegistryActivationStateV1.PROPOSED
            and isinstance(bootstrap_complete_payload, BootstrapCompletePayloadV1)
            and bootstrap_complete_payload.qualification_registry_snapshot_digest
            == self.snapshot_digest
        )
        if not seed_activated:
            raise ValueError("qualification registry is not active")
        completed = _parse_timestamp(
            bootstrap_complete_payload.completed_at,
            "bootstrap completion timestamp",
        )
        expires = _parse_timestamp(self.expires_at, "qualification registry expiry")
        if not completed <= instant < expires:
            raise ValueError("bootstrap-seed qualification registry is not current")
        return instant, True

    def compile_g0a_eligibility(
        self,
        *,
        qualification_registry_id: str,
        lineage_registry: LineageRegistryV1,
        current_snapshot_digest: str,
        current_lineage_snapshot_digest: str,
        expected_protocol_revision: str,
        expected_benchmark_harness_policy_revision: str,
        as_of: str,
        bootstrap_complete_payload: BootstrapCompletePayloadV1 | None = None,
    ) -> QualificationEligibilityV1:
        qualification_registry_id = _require_identifier(
            qualification_registry_id,
            "qualification registry id",
        )
        expected_protocol_revision = _require_identifier(
            expected_protocol_revision,
            "expected qualification protocol revision",
        )
        expected_benchmark_harness_policy_revision = _require_identifier(
            expected_benchmark_harness_policy_revision,
            "expected benchmark/harness policy revision",
        )
        instant, bootstrap_seed = self._assert_current(
            current_snapshot_digest=current_snapshot_digest,
            as_of=as_of,
            bootstrap_complete_payload=bootstrap_complete_payload,
        )
        _, lineage_bootstrap_seed = lineage_registry._assert_current(
            current_snapshot_digest=current_lineage_snapshot_digest,
            as_of=as_of,
            bootstrap_complete_payload=bootstrap_complete_payload,
        )
        if bootstrap_seed != lineage_bootstrap_seed:
            raise ValueError("registry activation modes disagree")
        if bootstrap_seed and (
            bootstrap_complete_payload is None
            or bootstrap_complete_payload.lineage_registry_snapshot_digest
            != lineage_registry.snapshot_digest
        ):
            raise ValueError("bootstrap completion lineage binding mismatch")

        matches = [
            entry for entry in self.entries
            if entry.qualification_registry_id == qualification_registry_id
        ]
        if len(matches) != 1:
            raise ValueError("qualification identity is missing or ambiguous")
        entry = matches[0]
        entry_current = (
            entry.is_bootstrap_seed_current_at(instant)
            if bootstrap_seed
            else entry.is_current_at(instant)
        )
        if not entry_current:
            raise ValueError("qualification identity is not active and current")
        if entry.protocol_revision != expected_protocol_revision:
            raise ValueError("qualification protocol binding is stale")
        if entry.protocol_revision != PROTOCOL_VERSION:
            raise ValueError("qualification cannot compile to the G0-A protocol")
        if (
            entry.benchmark_harness_policy_revision
            != expected_benchmark_harness_policy_revision
        ):
            raise ValueError("qualification benchmark/harness binding is stale")
        if entry.lineage_registry_snapshot_digest != lineage_registry.snapshot_digest:
            raise ValueError("qualification lineage snapshot binding is stale")

        actual_lineage = lineage_registry.require_current_entry(
            reviewer_registry_id=entry.reviewer_registry_id,
            current_snapshot_digest=current_lineage_snapshot_digest,
            as_of=as_of,
            bootstrap_complete_payload=bootstrap_complete_payload,
        )
        self._validate_exact_identity_binding(entry, actual_lineage)

        if entry.fallback_state is FallbackStateV1.NO_FALLBACK:
            if entry.requested_model_id not in actual_lineage.requested_model_aliases:
                raise ValueError("requested identity is absent from actual lineage entry")
        else:
            requested_lineage = lineage_registry.require_current_entry(
                reviewer_registry_id=entry.requested_reviewer_registry_id,
                current_snapshot_digest=current_lineage_snapshot_digest,
                as_of=as_of,
                bootstrap_complete_payload=bootstrap_complete_payload,
            )
            if entry.requested_model_id not in requested_lineage.requested_model_aliases:
                raise ValueError("fallback requested identity is not registered")
            if (
                actual_lineage.permitted_identity
                not in requested_lineage.permitted_fallback_identities
            ):
                raise ValueError("fallback actual identity is not policy permitted")

        return QualificationEligibilityV1(
            qualification_evidence_digest=entry.qualification_evidence_digest,
            actual_model_id=entry.actual_model_id,
            foundation_lineage_class=entry.foundation_lineage_class,
            qualified_reviewer_class=entry.qualified_reviewer_class,
            eligible_risk_levels=entry.eligible_risk_levels,
            protocol_revision=entry.protocol_revision,
            benchmark_harness_policy_revision=(
                entry.benchmark_harness_policy_revision
            ),
            qualification_registry_snapshot_digest=self.snapshot_digest,
            active=True,
        )

    @staticmethod
    def _validate_exact_identity_binding(
        entry: QualificationEntryV1,
        lineage: LineageIdentityEntryV1,
    ) -> None:
        exact_pairs = (
            (entry.authenticated_adapter_principal, lineage.authenticated_adapter_principal),
            (entry.provider_principal, lineage.provider_principal),
            (entry.provider_account_scope, lineage.provider_account_scope),
            (entry.serving_backend, lineage.serving_backend),
            (entry.endpoint_class, lineage.endpoint_class),
            (entry.actual_model_id, lineage.actual_model_id),
            (entry.identity_precision, lineage.identity_precision),
            (entry.foundation_model, lineage.foundation_model),
            (entry.foundation_revision, lineage.foundation_revision),
            (entry.foundation_lineage_class, lineage.foundation_lineage_class),
            (entry.execution_locality, lineage.execution_locality),
            (entry.data_egress_permitted, lineage.data_egress_permitted),
            (entry.actual_egress_destination, lineage.actual_egress_destination),
        )
        if any(actual != registered for actual, registered in exact_pairs):
            raise ValueError("qualification exact identity does not match lineage registry")
        if entry.authentication_method not in lineage.allowed_authentication_methods:
            raise ValueError("qualification authentication method is not registered")
        if entry.qualified_reviewer_class not in lineage.eligible_reviewer_classes:
            raise ValueError("qualification reviewer class exceeds lineage envelope")
        if not set(entry.eligible_risk_levels).issubset(lineage.eligible_risk_levels):
            raise ValueError("qualification risk envelope exceeds lineage envelope")


_QUALIFICATION_REGISTRY_KEYS = {
    "registry_version",
    "sequence_number",
    "previous_snapshot_digest",
    "policy_revision",
    "entries",
    "activation_state",
    "independent_review_record_digest",
    "created_at",
    "reviewed_at",
    "activated_at",
    "expires_at",
    "activation_record_digest",
    "ledger_sequence",
    "snapshot_digest",
}


__all__ = [
    "FallbackStateV1",
    "GENESIS_SNAPSHOT_DIGEST",
    "IdentityPrecisionV1",
    "LINEAGE_REGISTRY_VERSION",
    "LineageIdentityEntryV1",
    "LineageRegistryV1",
    "PermittedFallbackIdentityV1",
    "QUALIFICATION_REGISTRY_VERSION",
    "QualificationEntryV1",
    "QualificationRegistryV1",
    "QualificationStatusV1",
    "RegistryActivationStateV1",
]
