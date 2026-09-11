"""Review Mesh append-only ledger primitives and finding reducer.

G0-A Slice 4 implements deterministic, immutable values for:

- LEDGER_RECORD_V1
- hash-chain continuity
- monotonic ledger sequencing
- exact duplicate idempotency
- conflicting idempotency rejection
- typed tombstones
- material finding transition events
- exact repair-candidate binding
- independent-verification certificate binding
- finding lifecycle reduction

This module deliberately performs no persistent filesystem/database
write and no provider/model/network/runtime operation.

Durable atomic storage is a separate layer built on these contracts.
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    replace,
)
from datetime import datetime
from enum import Enum
import re
from typing import Mapping

from .review_mesh_protocol import (
    PROTOCOL_VERSION,
    canonical_digest,
)

from .review_mesh_decisions import (
    FindingState,
    InheritedFindingSetV1,
    MaterialFindingV1,
)


_SHA256 = re.compile(
    r"[a-f0-9]{64}"
)

_IDENTIFIER = re.compile(
    r"[A-Za-z0-9_.:/+@-]{1,256}"
)


def _require_sha256(
    value: str,
    label: str,
) -> str:
    value = str(value)

    if not _SHA256.fullmatch(value):
        raise ValueError(
            f"{label} must be a lowercase SHA-256 digest"
        )

    return value


def _require_identifier(
    value: str,
    label: str,
) -> str:
    value = str(value)

    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{label} is invalid"
        )

    return value


def _require_positive_u64(
    value: int,
    label: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value >= 2**64
    ):
        raise ValueError(
            f"{label} must be a positive unsigned 64-bit integer"
        )

    return value


def _require_timestamp(
    value: str,
    label: str,
) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError as error:
        raise ValueError(
            f"{label} must be RFC3339/ISO8601"
        ) from error

    if parsed.tzinfo is None:
        raise ValueError(
            f"{label} must be timezone-aware"
        )

    return parsed


LEDGER_GENESIS_DIGEST = canonical_digest({
    "protocol_version":
        PROTOCOL_VERSION,

    "ledger":
        "REVIEW_MESH_LEDGER_V1",

    "genesis":
        "PINNED_BOOTSTRAP_GENESIS_V1",
})


class LedgerRecordType(str, Enum):
    REVIEW_REQUEST = "REVIEW_REQUEST"
    IDENTITY_ENVELOPE = "IDENTITY_ENVELOPE"
    REVIEW_RESULT = "REVIEW_RESULT"
    RESULT_INGESTION = "RESULT_INGESTION"

    CANDIDATE_BINDING = "CANDIDATE_BINDING"
    FINDING_TRANSITION = "FINDING_TRANSITION"

    POLICY_DECISION = "POLICY_DECISION"
    QUALIFICATION_CHANGE = "QUALIFICATION_CHANGE"
    QUORUM_DECISION = "QUORUM_DECISION"

    TOMBSTONE = "TOMBSTONE"


class LedgerReconciliationError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class LedgerRecordV1:
    record_type: LedgerRecordType

    sequence_number: int
    previous_ledger_head_digest: str

    payload_digest: str

    related_task_id: str
    related_request_id: str | None
    related_campaign_id: str | None

    actor_provenance_digest: str
    ingestion_receipt_digest: str

    idempotency_key: str
    created_at: str

    superseded_or_revoked_record_digest: (
        str | None
    ) = None

    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        if (
            self.protocol_version
            != PROTOCOL_VERSION
        ):
            raise ValueError(
                "ledger protocol version mismatch"
            )

        if not isinstance(
            self.record_type,
            LedgerRecordType,
        ):
            raise ValueError(
                "ledger record type is invalid"
            )

        _require_positive_u64(
            self.sequence_number,
            "ledger sequence",
        )

        _require_sha256(
            self.previous_ledger_head_digest,
            "previous ledger head digest",
        )

        _require_sha256(
            self.payload_digest,
            "ledger payload digest",
        )

        _require_identifier(
            self.related_task_id,
            "related task id",
        )

        if self.related_request_id is not None:
            _require_identifier(
                self.related_request_id,
                "related request id",
            )

        if self.related_campaign_id is not None:
            _require_identifier(
                self.related_campaign_id,
                "related campaign id",
            )

        _require_sha256(
            self.actor_provenance_digest,
            "ledger actor provenance digest",
        )

        _require_sha256(
            self.ingestion_receipt_digest,
            "ledger ingestion receipt digest",
        )

        _require_identifier(
            self.idempotency_key,
            "ledger idempotency key",
        )

        _require_timestamp(
            self.created_at,
            "ledger record timestamp",
        )

        if (
            self.record_type
            is LedgerRecordType.TOMBSTONE
        ):
            if (
                self.superseded_or_revoked_record_digest
                is None
            ):
                raise ValueError(
                    "tombstone must reference an earlier record"
                )

            _require_sha256(
                self.superseded_or_revoked_record_digest,
                "tombstone target digest",
            )

        elif (
            self.superseded_or_revoked_record_digest
            is not None
        ):
            raise ValueError(
                "non-tombstone record cannot revoke another record"
            )

    def digest_mapping(self) -> dict:
        """Canonical bytes covered by record_digest.

        record_digest itself is deliberately absent.
        """

        return {
            "protocol_version":
                self.protocol_version,

            "record_type":
                self.record_type.value,

            "sequence_number":
                self.sequence_number,

            "previous_ledger_head_digest":
                self.previous_ledger_head_digest,

            "payload_digest":
                self.payload_digest,

            "related_task_id":
                self.related_task_id,

            "related_request_id":
                self.related_request_id,

            "related_campaign_id":
                self.related_campaign_id,

            "actor_provenance_digest":
                self.actor_provenance_digest,

            "ingestion_receipt_digest":
                self.ingestion_receipt_digest,

            "idempotency_key":
                self.idempotency_key,

            "created_at":
                self.created_at,

            "superseded_or_revoked_record_digest":
                self.superseded_or_revoked_record_digest,
        }

    @property
    def record_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.digest_mapping()
        )

    def to_mapping(self) -> dict:
        return {
            **self.digest_mapping(),
            "record_digest":
                self.record_digest,
        }

    def idempotency_fingerprint(
        self,
    ) -> str:
        """Logical delivery identity.

        Sequence, previous head and created_at are excluded because an
        exact retry may occur later after other records were appended.

        Material content/provenance must remain identical.
        """

        return canonical_digest({
            "protocol_version":
                self.protocol_version,

            "record_type":
                self.record_type.value,

            "payload_digest":
                self.payload_digest,

            "related_task_id":
                self.related_task_id,

            "related_request_id":
                self.related_request_id,

            "related_campaign_id":
                self.related_campaign_id,

            "actor_provenance_digest":
                self.actor_provenance_digest,

            "ingestion_receipt_digest":
                self.ingestion_receipt_digest,

            "idempotency_key":
                self.idempotency_key,

            "superseded_or_revoked_record_digest":
                self.superseded_or_revoked_record_digest,
        })


@dataclass(frozen=True)
class LedgerAppendOutcomeV1:
    ledger: "ReviewMeshLedgerV1"
    record: LedgerRecordV1
    duplicate: bool


@dataclass(frozen=True)
class ReviewMeshLedgerV1:
    records: tuple[
        LedgerRecordV1,
        ...
    ] = ()

    genesis_digest: str = (
        LEDGER_GENESIS_DIGEST
    )

    def __post_init__(self):
        if (
            self.genesis_digest
            != LEDGER_GENESIS_DIGEST
        ):
            raise LedgerReconciliationError(
                "unpinned ledger genesis"
            )

        previous = (
            self.genesis_digest
        )

        seen_digests = set()
        idempotency = {}

        for expected_sequence, record in enumerate(
            self.records,
            start=1,
        ):
            if (
                record.sequence_number
                != expected_sequence
            ):
                raise LedgerReconciliationError(
                    "ledger sequence gap or reorder"
                )

            if (
                record.previous_ledger_head_digest
                != previous
            ):
                raise LedgerReconciliationError(
                    "ledger previous-head mismatch"
                )

            if (
                record.record_digest
                in seen_digests
            ):
                raise LedgerReconciliationError(
                    "duplicate ledger record digest"
                )

            existing = idempotency.get(
                record.idempotency_key
            )

            if existing is not None:
                if (
                    existing.idempotency_fingerprint()
                    != record.idempotency_fingerprint()
                ):
                    raise LedgerReconciliationError(
                        "conflicting ledger idempotency key"
                    )

                raise LedgerReconciliationError(
                    "duplicate idempotency record in canonical chain"
                )

            if (
                record.record_type
                is LedgerRecordType.TOMBSTONE
            ):
                target = (
                    record
                    .superseded_or_revoked_record_digest
                )

                if target not in seen_digests:
                    raise LedgerReconciliationError(
                        "tombstone target is missing or not earlier"
                    )

            seen_digests.add(
                record.record_digest
            )

            idempotency[
                record.idempotency_key
            ] = record

            previous = (
                record.record_digest
            )

    @property
    def head_digest(
        self,
    ) -> str:
        if not self.records:
            return self.genesis_digest

        return (
            self.records[-1]
            .record_digest
        )

    @property
    def next_sequence(
        self,
    ) -> int:
        return (
            len(self.records)
            + 1
        )

    def verify_continuity(
        self,
    ) -> bool:
        # Construction already verifies the complete chain.
        ReviewMeshLedgerV1(
            records=self.records,
            genesis_digest=self.genesis_digest,
        )

        return True

    def append(
        self,
        *,
        record_type: LedgerRecordType,
        payload: Mapping,

        related_task_id: str,
        related_request_id: str | None,
        related_campaign_id: str | None,

        actor_provenance_digest: str,
        ingestion_receipt_digest: str,

        idempotency_key: str,
        created_at: str,

        superseded_or_revoked_record_digest: (
            str | None
        ) = None,
    ) -> LedgerAppendOutcomeV1:
        payload_digest = canonical_digest(
            payload
        )

        probe = LedgerRecordV1(
            record_type=record_type,

            sequence_number=(
                self.next_sequence
            ),

            previous_ledger_head_digest=(
                self.head_digest
            ),

            payload_digest=(
                payload_digest
            ),

            related_task_id=(
                related_task_id
            ),

            related_request_id=(
                related_request_id
            ),

            related_campaign_id=(
                related_campaign_id
            ),

            actor_provenance_digest=(
                actor_provenance_digest
            ),

            ingestion_receipt_digest=(
                ingestion_receipt_digest
            ),

            idempotency_key=(
                idempotency_key
            ),

            created_at=(
                created_at
            ),

            superseded_or_revoked_record_digest=(
                superseded_or_revoked_record_digest
            ),
        )

        for existing in self.records:
            if (
                existing.idempotency_key
                != idempotency_key
            ):
                continue

            if (
                existing.idempotency_fingerprint()
                != probe.idempotency_fingerprint()
            ):
                raise LedgerReconciliationError(
                    "conflicting ledger idempotency key"
                )

            return LedgerAppendOutcomeV1(
                ledger=self,
                record=existing,
                duplicate=True,
            )

        if (
            record_type
            is LedgerRecordType.TOMBSTONE
        ):
            target = (
                superseded_or_revoked_record_digest
            )

            if (
                target is None
                or target not in {
                    record.record_digest
                    for record in self.records
                }
            ):
                raise LedgerReconciliationError(
                    "tombstone target is missing or not earlier"
                )

        ledger = ReviewMeshLedgerV1(
            records=(
                self.records
                + (
                    probe,
                )
            ),
            genesis_digest=(
                self.genesis_digest
            ),
        )

        return LedgerAppendOutcomeV1(
            ledger=ledger,
            record=probe,
            duplicate=False,
        )


class FindingProofKind(str, Enum):
    REPAIR_EVIDENCE = "REPAIR_EVIDENCE"

    INDEPENDENT_VERIFICATION = (
        "INDEPENDENT_VERIFICATION"
    )

    DETERMINISTIC_FAILURE = (
        "DETERMINISTIC_FAILURE"
    )

    QUALIFIED_INDEPENDENT_FINDING = (
        "QUALIFIED_INDEPENDENT_FINDING"
    )


@dataclass(frozen=True)
class IndependentVerificationSetV1:
    """Trusted upstream verification certificate.

    Slice 4 binds this certificate into finding transitions.
    Qualification/quorum construction of this certificate comes later.
    """

    candidate_generation: int
    candidate_identity_digest: str

    verifier_identity_envelope_digests: tuple[
        str,
        ...
    ]

    verification_evidence_digest: str

    independence_decision_digest: str
    qualification_decision_digest: str
    policy_decision_digest: str

    def __post_init__(self):
        _require_positive_u64(
            self.candidate_generation,
            "verification candidate generation",
        )

        _require_sha256(
            self.candidate_identity_digest,
            "verification candidate identity digest",
        )

        if not (
            self.verifier_identity_envelope_digests
        ):
            raise ValueError(
                "verification set must contain a verifier"
            )

        if (
            len(
                set(
                    self.verifier_identity_envelope_digests
                )
            )
            != len(
                self.verifier_identity_envelope_digests
            )
        ):
            raise ValueError(
                "verification set contains duplicate verifier"
            )

        for value in (
            self.verifier_identity_envelope_digests
        ):
            _require_sha256(
                value,
                "verifier identity envelope digest",
            )

        for label, value in (
            (
                "verification evidence digest",
                self.verification_evidence_digest,
            ),
            (
                "independence decision digest",
                self.independence_decision_digest,
            ),
            (
                "qualification decision digest",
                self.qualification_decision_digest,
            ),
            (
                "verification policy decision digest",
                self.policy_decision_digest,
            ),
        ):
            _require_sha256(
                value,
                label,
            )

    def stable_mapping(self) -> dict:
        return {
            "candidate_generation":
                self.candidate_generation,

            "candidate_identity_digest":
                self.candidate_identity_digest,

            "verifier_identity_envelope_digests":
                sorted(
                    self.verifier_identity_envelope_digests
                ),

            "verification_evidence_digest":
                self.verification_evidence_digest,

            "independence_decision_digest":
                self.independence_decision_digest,

            "qualification_decision_digest":
                self.qualification_decision_digest,

            "policy_decision_digest":
                self.policy_decision_digest,
        }

    @property
    def verification_set_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )


_ALLOWED_FINDING_TRANSITIONS = frozenset({
    (
        FindingState.OPEN,
        FindingState.REPAIR_PROPOSED,
    ),
    (
        FindingState.REOPENED,
        FindingState.REPAIR_PROPOSED,
    ),
    (
        FindingState.REPAIR_PROPOSED,
        FindingState.VERIFIED_CLOSED,
    ),
    (
        FindingState.OPEN,
        FindingState.DISMISSED,
    ),
    (
        FindingState.REPAIR_PROPOSED,
        FindingState.DISMISSED,
    ),
    (
        FindingState.REOPENED,
        FindingState.DISMISSED,
    ),
    (
        FindingState.VERIFIED_CLOSED,
        FindingState.REOPENED,
    ),
    (
        FindingState.DISMISSED,
        FindingState.REOPENED,
    ),
})


@dataclass(frozen=True)
class FindingTransitionEventV1:
    finding_id: str

    from_state: FindingState
    to_state: FindingState

    candidate_generation: int
    candidate_identity_digest: str

    actor_identity_envelope_digest: str

    proof_kind: FindingProofKind
    evidence_digest: str

    fixer_identity_envelope_digest: (
        str | None
    ) = None

    verification_set: (
        IndependentVerificationSetV1
        | None
    ) = None

    created_at: str = ""

    protocol_version: str = PROTOCOL_VERSION

    def __post_init__(self):
        if (
            self.protocol_version
            != PROTOCOL_VERSION
        ):
            raise ValueError(
                "finding transition protocol version mismatch"
            )

        _require_identifier(
            self.finding_id,
            "finding id",
        )

        if not isinstance(
            self.from_state,
            FindingState,
        ):
            raise ValueError(
                "finding from-state is invalid"
            )

        if not isinstance(
            self.to_state,
            FindingState,
        ):
            raise ValueError(
                "finding to-state is invalid"
            )

        if (
            self.from_state,
            self.to_state,
        ) not in _ALLOWED_FINDING_TRANSITIONS:
            raise ValueError(
                "finding transition is not allowed"
            )

        _require_positive_u64(
            self.candidate_generation,
            "finding transition candidate generation",
        )

        _require_sha256(
            self.candidate_identity_digest,
            "finding transition candidate identity digest",
        )

        _require_sha256(
            self.actor_identity_envelope_digest,
            "finding transition actor identity digest",
        )

        if not isinstance(
            self.proof_kind,
            FindingProofKind,
        ):
            raise ValueError(
                "finding transition proof kind is invalid"
            )

        _require_sha256(
            self.evidence_digest,
            "finding transition evidence digest",
        )

        if (
            self.fixer_identity_envelope_digest
            is not None
        ):
            _require_sha256(
                self.fixer_identity_envelope_digest,
                "finding transition fixer identity digest",
            )

        _require_timestamp(
            self.created_at,
            "finding transition timestamp",
        )

        if (
            self.to_state
            is FindingState.REPAIR_PROPOSED
        ):
            if (
                self.proof_kind
                is not FindingProofKind.REPAIR_EVIDENCE
            ):
                raise ValueError(
                    "repair proposal requires repair evidence"
                )

            if (
                self.fixer_identity_envelope_digest
                is None
            ):
                raise ValueError(
                    "repair proposal requires fixer identity"
                )

            if self.verification_set is not None:
                raise ValueError(
                    "repair proposal cannot contain closure verification"
                )

        elif self.to_state in (
            FindingState.VERIFIED_CLOSED,
            FindingState.DISMISSED,
        ):
            if (
                self.proof_kind
                is not FindingProofKind
                .INDEPENDENT_VERIFICATION
            ):
                raise ValueError(
                    "closure/dismissal requires independent verification"
                )

            if self.verification_set is None:
                raise ValueError(
                    "closure/dismissal requires verification set"
                )

            if (
                self.fixer_identity_envelope_digest
                is not None
            ):
                raise ValueError(
                    "closure/dismissal event cannot claim fixer role"
                )

            self._validate_verification_binding()

        elif (
            self.to_state
            is FindingState.REOPENED
        ):
            if self.proof_kind not in (
                FindingProofKind
                .DETERMINISTIC_FAILURE,

                FindingProofKind
                .QUALIFIED_INDEPENDENT_FINDING,
            ):
                raise ValueError(
                    "reopen requires deterministic or independent proof"
                )

            if (
                self.proof_kind
                is FindingProofKind
                .DETERMINISTIC_FAILURE
            ):
                if self.verification_set is not None:
                    raise ValueError(
                        "deterministic reopen cannot carry reviewer verification"
                    )

            else:
                if self.verification_set is None:
                    raise ValueError(
                        "independent reopen requires verification set"
                    )

                self._validate_verification_binding()

    def _validate_verification_binding(
        self,
    ) -> None:
        assert (
            self.verification_set
            is not None
        )

        if (
            self.verification_set
            .candidate_generation
            != self.candidate_generation
        ):
            raise ValueError(
                "verification set candidate generation mismatch"
            )

        if (
            self.verification_set
            .candidate_identity_digest
            != self.candidate_identity_digest
        ):
            raise ValueError(
                "verification set candidate identity mismatch"
            )

    def stable_mapping(self) -> dict:
        return {
            "protocol_version":
                self.protocol_version,

            "finding_id":
                self.finding_id,

            "from_state":
                self.from_state.value,

            "to_state":
                self.to_state.value,

            "candidate_generation":
                self.candidate_generation,

            "candidate_identity_digest":
                self.candidate_identity_digest,

            "actor_identity_envelope_digest":
                self.actor_identity_envelope_digest,

            "proof_kind":
                self.proof_kind.value,

            "evidence_digest":
                self.evidence_digest,

            "fixer_identity_envelope_digest":
                self.fixer_identity_envelope_digest,

            "verification_set_digest":
                (
                    None
                    if self.verification_set is None
                    else
                    self.verification_set
                    .verification_set_digest
                ),

            "created_at":
                self.created_at,
        }

    @property
    def event_digest(
        self,
    ) -> str:
        return canonical_digest(
            self.stable_mapping()
        )


@dataclass(frozen=True)
class FindingLifecycleEntryV1:
    finding: MaterialFindingV1

    last_candidate_generation: int
    last_candidate_identity_digest: str

    repair_candidate_generation: (
        int | None
    ) = None

    repair_candidate_identity_digest: (
        str | None
    ) = None

    repair_fixer_identity_digest: (
        str | None
    ) = None

    last_transition_event_digest: (
        str | None
    ) = None

    transition_count: int = 0

    def __post_init__(self):
        _require_positive_u64(
            self.last_candidate_generation,
            "finding lifecycle candidate generation",
        )

        _require_sha256(
            self.last_candidate_identity_digest,
            "finding lifecycle candidate identity digest",
        )

        if (
            self.last_candidate_generation
            < self.finding
            .candidate_generation_introduced
        ):
            raise ValueError(
                "finding lifecycle predates finding introduction"
            )

        repair_values = (
            self.repair_candidate_generation,
            self.repair_candidate_identity_digest,
            self.repair_fixer_identity_digest,
        )

        if any(
            value is not None
            for value in repair_values
        ):
            if not all(
                value is not None
                for value in repair_values
            ):
                raise ValueError(
                    "repair binding is incomplete"
                )

            _require_positive_u64(
                self.repair_candidate_generation,
                "repair candidate generation",
            )

            _require_sha256(
                self.repair_candidate_identity_digest,
                "repair candidate identity digest",
            )

            _require_sha256(
                self.repair_fixer_identity_digest,
                "repair fixer identity digest",
            )

        if (
            self.last_transition_event_digest
            is not None
        ):
            _require_sha256(
                self.last_transition_event_digest,
                "last finding transition digest",
            )

        if (
            isinstance(self.transition_count, bool)
            or not isinstance(
                self.transition_count,
                int,
            )
            or self.transition_count < 0
        ):
            raise ValueError(
                "finding transition count is invalid"
            )

    @property
    def state(
        self,
    ) -> FindingState:
        return self.finding.state

    def stable_mapping(self) -> dict:
        return {
            "finding":
                self.finding.stable_mapping(),

            "last_candidate_generation":
                self.last_candidate_generation,

            "last_candidate_identity_digest":
                self.last_candidate_identity_digest,

            "repair_candidate_generation":
                self.repair_candidate_generation,

            "repair_candidate_identity_digest":
                self.repair_candidate_identity_digest,

            "repair_fixer_identity_digest":
                self.repair_fixer_identity_digest,

            "last_transition_event_digest":
                self.last_transition_event_digest,

            "transition_count":
                self.transition_count,
        }


@dataclass(frozen=True)
class FindingLifecycleStateV1:
    entries: tuple[
        FindingLifecycleEntryV1,
        ...
    ]

    def __post_init__(self):
        seen = set()

        for entry in self.entries:
            finding_id = (
                entry.finding.finding_id
            )

            if finding_id in seen:
                raise ValueError(
                    "duplicate finding lifecycle entry"
                )

            seen.add(
                finding_id
            )

    @classmethod
    def from_inherited(
        cls,
        findings: InheritedFindingSetV1,
        *,
        current_candidate_generation: int,
        current_candidate_identity_digest: str,
    ) -> "FindingLifecycleStateV1":
        _require_positive_u64(
            current_candidate_generation,
            "current candidate generation",
        )

        _require_sha256(
            current_candidate_identity_digest,
            "current candidate identity digest",
        )

        entries = []

        for finding in findings.findings:
            if (
                current_candidate_generation
                < finding
                .candidate_generation_introduced
            ):
                raise ValueError(
                    "current candidate predates inherited finding"
                )

            entries.append(
                FindingLifecycleEntryV1(
                    finding=finding,

                    last_candidate_generation=(
                        current_candidate_generation
                    ),

                    last_candidate_identity_digest=(
                        current_candidate_identity_digest
                    ),
                )
            )

        return cls(
            tuple(
                sorted(
                    entries,
                    key=lambda item: (
                        item.finding.finding_id
                    ),
                )
            )
        )

    @property
    def lifecycle_digest(
        self,
    ) -> str:
        return canonical_digest({
            "entries": [
                entry.stable_mapping()
                for entry in self.entries
            ]
        })

    @property
    def unresolved(
        self,
    ) -> tuple[
        MaterialFindingV1,
        ...
    ]:
        return tuple(
            entry.finding
            for entry in self.entries
            if entry.finding.unresolved
        )

    def apply(
        self,
        event: FindingTransitionEventV1,
    ) -> "FindingLifecycleStateV1":
        index = None

        for position, entry in enumerate(
            self.entries
        ):
            if (
                entry.finding.finding_id
                == event.finding_id
            ):
                index = position
                break

        if index is None:
            raise ValueError(
                "finding transition references unknown finding"
            )

        current = self.entries[
            index
        ]

        if (
            current.state
            is not event.from_state
        ):
            raise ValueError(
                "finding transition from-state mismatch"
            )

        updated = self._reduce_entry(
            current,
            event,
        )

        entries = list(
            self.entries
        )

        entries[index] = updated

        return FindingLifecycleStateV1(
            tuple(entries)
        )

    @staticmethod
    def _reduce_entry(
        current: FindingLifecycleEntryV1,
        event: FindingTransitionEventV1,
    ) -> FindingLifecycleEntryV1:
        if (
            event.to_state
            is FindingState.REPAIR_PROPOSED
        ):
            if (
                event.candidate_generation
                <= current
                .last_candidate_generation
            ):
                raise ValueError(
                    "repair proposal requires a new candidate generation"
                )

            assert (
                event
                .fixer_identity_envelope_digest
                is not None
            )

            new_finding = replace(
                current.finding,
                state=FindingState.REPAIR_PROPOSED,
            )

            return FindingLifecycleEntryV1(
                finding=new_finding,

                last_candidate_generation=(
                    event.candidate_generation
                ),

                last_candidate_identity_digest=(
                    event.candidate_identity_digest
                ),

                repair_candidate_generation=(
                    event.candidate_generation
                ),

                repair_candidate_identity_digest=(
                    event.candidate_identity_digest
                ),

                repair_fixer_identity_digest=(
                    event
                    .fixer_identity_envelope_digest
                ),

                last_transition_event_digest=(
                    event.event_digest
                ),

                transition_count=(
                    current.transition_count
                    + 1
                ),
            )

        if (
            event.to_state
            is FindingState.VERIFIED_CLOSED
        ):
            if (
                current.repair_candidate_generation
                is None
                or current
                .repair_candidate_identity_digest
                is None
                or current
                .repair_fixer_identity_digest
                is None
            ):
                raise ValueError(
                    "closure requires an exact repair binding"
                )

            if (
                event.candidate_generation
                != current
                .repair_candidate_generation
                or event.candidate_identity_digest
                != current
                .repair_candidate_identity_digest
            ):
                raise ValueError(
                    "closure does not bind exact repair candidate"
                )

            assert (
                event.verification_set
                is not None
            )

            if (
                current
                .repair_fixer_identity_digest
                in event
                .verification_set
                .verifier_identity_envelope_digests
            ):
                raise ValueError(
                    "fixer cannot verify its own repair"
                )

            new_finding = replace(
                current.finding,
                state=FindingState.VERIFIED_CLOSED,
            )

            return replace(
                current,

                finding=new_finding,

                last_transition_event_digest=(
                    event.event_digest
                ),

                transition_count=(
                    current.transition_count
                    + 1
                ),
            )

        if (
            event.to_state
            is FindingState.DISMISSED
        ):
            if (
                event.candidate_generation
                < current
                .last_candidate_generation
            ):
                raise ValueError(
                    "dismissal cannot bind an older candidate"
                )

            assert (
                event.verification_set
                is not None
            )

            if (
                current
                .repair_fixer_identity_digest
                is not None
                and current
                .repair_fixer_identity_digest
                in event
                .verification_set
                .verifier_identity_envelope_digests
            ):
                raise ValueError(
                    "fixer cannot verify dismissal of its own repair"
                )

            new_finding = replace(
                current.finding,
                state=FindingState.DISMISSED,
            )

            return FindingLifecycleEntryV1(
                finding=new_finding,

                last_candidate_generation=(
                    event.candidate_generation
                ),

                last_candidate_identity_digest=(
                    event.candidate_identity_digest
                ),

                repair_candidate_generation=(
                    current
                    .repair_candidate_generation
                ),

                repair_candidate_identity_digest=(
                    current
                    .repair_candidate_identity_digest
                ),

                repair_fixer_identity_digest=(
                    current
                    .repair_fixer_identity_digest
                ),

                last_transition_event_digest=(
                    event.event_digest
                ),

                transition_count=(
                    current.transition_count
                    + 1
                ),
            )

        if (
            event.to_state
            is FindingState.REOPENED
        ):
            if (
                event.candidate_generation
                < current
                .last_candidate_generation
            ):
                raise ValueError(
                    "reopen cannot bind an older candidate"
                )

            new_finding = replace(
                current.finding,
                state=FindingState.REOPENED,
            )

            return FindingLifecycleEntryV1(
                finding=new_finding,

                last_candidate_generation=(
                    event.candidate_generation
                ),

                last_candidate_identity_digest=(
                    event.candidate_identity_digest
                ),

                repair_candidate_generation=None,
                repair_candidate_identity_digest=None,
                repair_fixer_identity_digest=None,

                last_transition_event_digest=(
                    event.event_digest
                ),

                transition_count=(
                    current.transition_count
                    + 1
                ),
            )

        raise ValueError(
            "unsupported finding transition"
        )
