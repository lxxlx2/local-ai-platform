"""Crash-safe durable storage for Review Mesh Ledger V1.

G0-A Slice 5 provides a generic path-injected Owner-private store.

Important boundaries:

- no production path is hard-coded;
- no model/provider/network integration exists;
- callers explicitly choose the storage path;
- tests use temporary directories only;
- logical ledger history remains append-only;
- physical persistence uses atomic full-snapshot replacement;
- each durable entry retains both immutable record header and the
  canonical payload whose digest is bound by that record.

Atomic write sequence:

1. acquire exclusive advisory writer lock;
2. load and verify current complete ledger;
3. compare expected head if supplied;
4. construct next immutable record;
5. write complete next snapshot to sibling temporary file;
6. fsync temporary file;
7. atomically replace canonical snapshot;
8. fsync parent directory;
9. reload and verify the durable result.

A crash can therefore expose the previous complete snapshot or the next
complete snapshot. Partial JSON is never intentionally installed as the
canonical store file.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable, Mapping

from .review_mesh_protocol import (
    PROTOCOL_VERSION,
    canonical_digest,
    canonical_json_bytes,
)

from .review_mesh_ledger import (
    LEDGER_GENESIS_DIGEST,
    LedgerAppendOutcomeV1,
    LedgerRecordType,
    LedgerRecordV1,
    LedgerReconciliationError,
    ReviewMeshLedgerV1,
)


STORE_SCHEMA_VERSION = (
    "REVIEW_MESH_LEDGER_STORE_V1"
)

FaultHook = Callable[[str], None]


def _canonical_payload_json(
    payload: Mapping,
) -> str:
    if not isinstance(
        payload,
        Mapping,
    ):
        raise ValueError(
            "ledger payload must be a mapping"
        )

    return canonical_json_bytes(
        payload
    ).decode("utf-8")


@dataclass(frozen=True)
class StoredLedgerEntryV1:
    """One immutable ledger record plus its canonical payload."""

    record: LedgerRecordV1
    payload_json: str

    def __post_init__(self):
        try:
            payload = json.loads(
                self.payload_json
            )
        except json.JSONDecodeError as error:
            raise LedgerReconciliationError(
                "stored ledger payload is invalid JSON"
            ) from error

        if not isinstance(
            payload,
            dict,
        ):
            raise LedgerReconciliationError(
                "stored ledger payload must be an object"
            )

        canonical = (
            canonical_json_bytes(
                payload
            ).decode("utf-8")
        )

        if canonical != self.payload_json:
            raise LedgerReconciliationError(
                "stored ledger payload is not canonical"
            )

        if (
            canonical_digest(payload)
            != self.record.payload_digest
        ):
            raise LedgerReconciliationError(
                "stored ledger payload digest mismatch"
            )

    @classmethod
    def from_payload(
        cls,
        record: LedgerRecordV1,
        payload: Mapping,
    ) -> "StoredLedgerEntryV1":
        return cls(
            record=record,
            payload_json=(
                _canonical_payload_json(
                    payload
                )
            ),
        )

    def payload_mapping(
        self,
    ) -> dict:
        value = json.loads(
            self.payload_json
        )

        assert isinstance(
            value,
            dict,
        )

        return value

    def to_mapping(
        self,
    ) -> dict:
        return {
            "record":
                self.record.to_mapping(),

            "payload":
                self.payload_mapping(),
        }


@dataclass(frozen=True)
class ReviewMeshLedgerSnapshotV1:
    entries: tuple[
        StoredLedgerEntryV1,
        ...
    ] = ()

    def __post_init__(self):
        # Reconstructing the pure ledger verifies:
        # sequence, previous-head chain, genesis, idempotency and
        # tombstone ordering.
        ReviewMeshLedgerV1(
            records=tuple(
                entry.record
                for entry in self.entries
            )
        )

        record_digests = [
            entry.record.record_digest
            for entry in self.entries
        ]

        if (
            len(record_digests)
            != len(set(record_digests))
        ):
            raise LedgerReconciliationError(
                "durable ledger contains duplicate record digest"
            )

    @property
    def ledger(
        self,
    ) -> ReviewMeshLedgerV1:
        return ReviewMeshLedgerV1(
            records=tuple(
                entry.record
                for entry in self.entries
            )
        )

    @property
    def head_digest(
        self,
    ) -> str:
        return self.ledger.head_digest

    @property
    def record_count(
        self,
    ) -> int:
        return len(
            self.entries
        )

    def payload_for_record(
        self,
        record_digest: str,
    ) -> dict:
        matches = [
            entry
            for entry in self.entries
            if (
                entry.record.record_digest
                == record_digest
            )
        ]

        if len(matches) != 1:
            raise KeyError(
                "ledger record payload not found"
            )

        return (
            matches[0]
            .payload_mapping()
        )

    def to_mapping(
        self,
    ) -> dict:
        return {
            "schema_version":
                STORE_SCHEMA_VERSION,

            "protocol_version":
                PROTOCOL_VERSION,

            "genesis_digest":
                LEDGER_GENESIS_DIGEST,

            "record_count":
                self.record_count,

            "head_digest":
                self.head_digest,

            "entries": [
                entry.to_mapping()
                for entry in self.entries
            ],
        }


@dataclass(frozen=True)
class DurableLedgerAppendOutcomeV1:
    snapshot: ReviewMeshLedgerSnapshotV1
    record: LedgerRecordV1
    duplicate: bool
    persisted: bool


_RECORD_KEYS = {
    "protocol_version",
    "record_type",
    "sequence_number",
    "previous_ledger_head_digest",
    "payload_digest",
    "related_task_id",
    "related_request_id",
    "related_campaign_id",
    "actor_provenance_digest",
    "ingestion_receipt_digest",
    "idempotency_key",
    "created_at",
    "superseded_or_revoked_record_digest",
    "record_digest",
}


def _record_from_mapping(
    raw: object,
) -> LedgerRecordV1:
    if (
        not isinstance(raw, dict)
        or set(raw) != _RECORD_KEYS
    ):
        raise LedgerReconciliationError(
            "invalid durable ledger record schema"
        )

    string_fields = (
        "protocol_version",
        "record_type",
        "previous_ledger_head_digest",
        "payload_digest",
        "related_task_id",
        "actor_provenance_digest",
        "ingestion_receipt_digest",
        "idempotency_key",
        "created_at",
        "record_digest",
    )

    for field in string_fields:
        if not isinstance(
            raw[field],
            str,
        ):
            raise LedgerReconciliationError(
                f"durable ledger record field {field} has invalid type"
            )

    if (
        not isinstance(
            raw["sequence_number"],
            int,
        )
        or isinstance(
            raw["sequence_number"],
            bool,
        )
    ):
        raise LedgerReconciliationError(
            "durable ledger sequence has invalid type"
        )

    for field in (
        "related_request_id",
        "related_campaign_id",
        "superseded_or_revoked_record_digest",
    ):
        if (
            raw[field] is not None
            and not isinstance(
                raw[field],
                str,
            )
        ):
            raise LedgerReconciliationError(
                f"durable ledger optional field {field} has invalid type"
            )

    try:
        record = LedgerRecordV1(
            protocol_version=(
                raw["protocol_version"]
            ),

            record_type=(
                LedgerRecordType(
                    raw["record_type"]
                )
            ),

            sequence_number=(
                raw["sequence_number"]
            ),

            previous_ledger_head_digest=(
                raw[
                    "previous_ledger_head_digest"
                ]
            ),

            payload_digest=(
                raw["payload_digest"]
            ),

            related_task_id=(
                raw["related_task_id"]
            ),

            related_request_id=(
                raw["related_request_id"]
            ),

            related_campaign_id=(
                raw["related_campaign_id"]
            ),

            actor_provenance_digest=(
                raw[
                    "actor_provenance_digest"
                ]
            ),

            ingestion_receipt_digest=(
                raw[
                    "ingestion_receipt_digest"
                ]
            ),

            idempotency_key=(
                raw["idempotency_key"]
            ),

            created_at=(
                raw["created_at"]
            ),

            superseded_or_revoked_record_digest=(
                raw[
                    "superseded_or_revoked_record_digest"
                ]
            ),
        )

    except (
        TypeError,
        ValueError,
    ) as error:
        raise LedgerReconciliationError(
            f"invalid durable ledger record: {error}"
        ) from error

    if (
        raw["record_digest"]
        != record.record_digest
    ):
        raise LedgerReconciliationError(
            "durable ledger record digest mismatch"
        )

    return record


def _entry_from_mapping(
    raw: object,
) -> StoredLedgerEntryV1:
    if (
        not isinstance(raw, dict)
        or set(raw)
        != {
            "record",
            "payload",
        }
    ):
        raise LedgerReconciliationError(
            "invalid durable ledger entry schema"
        )

    if not isinstance(
        raw["payload"],
        dict,
    ):
        raise LedgerReconciliationError(
            "durable ledger entry payload must be an object"
        )

    record = _record_from_mapping(
        raw["record"]
    )

    return (
        StoredLedgerEntryV1
        .from_payload(
            record,
            raw["payload"],
        )
    )


class ReviewMeshLedgerStoreV1:
    """Path-injected crash-safe Owner-private ledger store."""

    def __init__(
        self,
        path: Path | str,
        *,
        fault_hook: FaultHook | None = None,
    ):
        self.path = Path(path)

        if not self.path.name:
            raise ValueError(
                "ledger store path is invalid"
            )

        self.lock_path = (
            self.path.with_name(
                self.path.name
                + ".lock"
            )
        )

        self._fault_hook = (
            fault_hook
        )

    def _fault(
        self,
        stage: str,
    ) -> None:
        if self._fault_hook is not None:
            self._fault_hook(
                stage
            )

    @staticmethod
    def _assert_private_regular_file(
        path: Path,
    ) -> None:
        if path.is_symlink():
            raise LedgerReconciliationError(
                "ledger snapshot symlink is forbidden"
            )

        try:
            info = path.stat()
        except OSError as error:
            raise LedgerReconciliationError(
                "cannot stat durable ledger snapshot"
            ) from error

        if not stat.S_ISREG(
            info.st_mode
        ):
            raise LedgerReconciliationError(
                "durable ledger snapshot is not a regular file"
            )

        if (
            stat.S_IMODE(
                info.st_mode
            )
            & 0o077
        ):
            raise LedgerReconciliationError(
                "durable ledger snapshot permissions are not owner-private"
            )

    def _decode_snapshot(
        self,
        raw: object,
    ) -> ReviewMeshLedgerSnapshotV1:
        required = {
            "schema_version",
            "protocol_version",
            "genesis_digest",
            "record_count",
            "head_digest",
            "entries",
        }

        if (
            not isinstance(raw, dict)
            or set(raw) != required
        ):
            raise LedgerReconciliationError(
                "invalid durable ledger top-level schema"
            )

        if (
            raw["schema_version"]
            != STORE_SCHEMA_VERSION
        ):
            raise LedgerReconciliationError(
                "durable ledger schema version mismatch"
            )

        if (
            raw["protocol_version"]
            != PROTOCOL_VERSION
        ):
            raise LedgerReconciliationError(
                "durable ledger protocol version mismatch"
            )

        if (
            raw["genesis_digest"]
            != LEDGER_GENESIS_DIGEST
        ):
            raise LedgerReconciliationError(
                "durable ledger genesis mismatch"
            )

        if (
            not isinstance(
                raw["record_count"],
                int,
            )
            or isinstance(
                raw["record_count"],
                bool,
            )
            or raw["record_count"] < 0
        ):
            raise LedgerReconciliationError(
                "durable ledger record count is invalid"
            )

        if not isinstance(
            raw["head_digest"],
            str,
        ):
            raise LedgerReconciliationError(
                "durable ledger head digest has invalid type"
            )

        if not isinstance(
            raw["entries"],
            list,
        ):
            raise LedgerReconciliationError(
                "durable ledger entries must be a list"
            )

        entries = tuple(
            _entry_from_mapping(
                item
            )
            for item in raw["entries"]
        )

        snapshot = (
            ReviewMeshLedgerSnapshotV1(
                entries
            )
        )

        if (
            raw["record_count"]
            != snapshot.record_count
        ):
            raise LedgerReconciliationError(
                "durable ledger record count mismatch"
            )

        if (
            raw["head_digest"]
            != snapshot.head_digest
        ):
            raise LedgerReconciliationError(
                "durable ledger head digest mismatch"
            )

        return snapshot

    def _load_unlocked(
        self,
    ) -> ReviewMeshLedgerSnapshotV1:
        if not self.path.exists():
            if self.path.is_symlink():
                raise LedgerReconciliationError(
                    "ledger snapshot symlink is forbidden"
                )

            return (
                ReviewMeshLedgerSnapshotV1()
            )

        self._assert_private_regular_file(
            self.path
        )

        try:
            payload = self.path.read_bytes()
        except OSError as error:
            raise LedgerReconciliationError(
                "cannot read durable ledger snapshot"
            ) from error

        try:
            raw = json.loads(
                payload.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise LedgerReconciliationError(
                "durable ledger snapshot is invalid JSON"
            ) from error

        return self._decode_snapshot(
            raw
        )

    def load(
        self,
    ) -> ReviewMeshLedgerSnapshotV1:
        """Read and verify a complete old-or-new atomic snapshot."""

        return self._load_unlocked()

    @contextmanager
    def _writer_lock(
        self,
    ):
        parent = self.path.parent

        if not parent.exists():
            raise LedgerReconciliationError(
                "ledger store parent directory does not exist"
            )

        if not parent.is_dir():
            raise LedgerReconciliationError(
                "ledger store parent is not a directory"
            )

        if self.lock_path.is_symlink():
            raise LedgerReconciliationError(
                "ledger lock symlink is forbidden"
            )

        flags = (
            os.O_RDWR
            | os.O_CREAT
        )

        if hasattr(
            os,
            "O_CLOEXEC",
        ):
            flags |= os.O_CLOEXEC

        if hasattr(
            os,
            "O_NOFOLLOW",
        ):
            flags |= os.O_NOFOLLOW

        try:
            fd = os.open(
                self.lock_path,
                flags,
                0o600,
            )
        except OSError as error:
            raise LedgerReconciliationError(
                "cannot open ledger writer lock"
            ) from error

        try:
            os.fchmod(
                fd,
                0o600,
            )

            info = os.fstat(
                fd
            )

            if not stat.S_ISREG(
                info.st_mode
            ):
                raise LedgerReconciliationError(
                    "ledger writer lock is not a regular file"
                )

            fcntl.flock(
                fd,
                fcntl.LOCK_EX,
            )

            yield

        finally:
            try:
                fcntl.flock(
                    fd,
                    fcntl.LOCK_UN,
                )
            finally:
                os.close(
                    fd
                )

    def _atomic_write(
        self,
        snapshot: ReviewMeshLedgerSnapshotV1,
    ) -> None:
        parent = self.path.parent

        if self.path.is_symlink():
            raise LedgerReconciliationError(
                "ledger snapshot symlink is forbidden"
            )

        payload = (
            canonical_json_bytes(
                snapshot.to_mapping()
            )
            + b"\n"
        )

        temp_fd = None
        temp_name = None

        try:
            temp_fd, temp_name = (
                tempfile.mkstemp(
                    prefix=(
                        "."
                        + self.path.name
                        + ".tmp-"
                    ),
                    dir=parent,
                )
            )

            os.fchmod(
                temp_fd,
                0o600,
            )

            with os.fdopen(
                temp_fd,
                "wb",
                closefd=True,
            ) as handle:
                temp_fd = None

                handle.write(
                    payload
                )

                handle.flush()

                os.fsync(
                    handle.fileno()
                )

            self._fault(
                "AFTER_TEMP_FSYNC"
            )

            os.replace(
                temp_name,
                self.path,
            )

            temp_name = None

            self._fault(
                "AFTER_REPLACE"
            )

            directory_fd = os.open(
                parent,
                os.O_RDONLY,
            )

            try:
                os.fsync(
                    directory_fd
                )
            finally:
                os.close(
                    directory_fd
                )

            self._fault(
                "AFTER_DIRECTORY_FSYNC"
            )

        finally:
            if temp_fd is not None:
                os.close(
                    temp_fd
                )

            if (
                temp_name is not None
                and os.path.exists(
                    temp_name
                )
            ):
                try:
                    os.unlink(
                        temp_name
                    )
                except OSError:
                    pass

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

        expected_head_digest: str | None = None,

        superseded_or_revoked_record_digest: (
            str | None
        ) = None,
    ) -> DurableLedgerAppendOutcomeV1:
        canonical_payload = json.loads(
            _canonical_payload_json(
                payload
            )
        )

        assert isinstance(
            canonical_payload,
            dict,
        )

        with self._writer_lock():
            current = (
                self._load_unlocked()
            )

            if (
                expected_head_digest
                is not None
                and expected_head_digest
                != current.head_digest
            ):
                raise LedgerReconciliationError(
                    "stale expected ledger head"
                )

            outcome: LedgerAppendOutcomeV1 = (
                current.ledger.append(
                    record_type=record_type,

                    payload=(
                        canonical_payload
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
            )

            if outcome.duplicate:
                return (
                    DurableLedgerAppendOutcomeV1(
                        snapshot=current,
                        record=outcome.record,
                        duplicate=True,
                        persisted=False,
                    )
                )

            entry = (
                StoredLedgerEntryV1
                .from_payload(
                    outcome.record,
                    canonical_payload,
                )
            )

            next_snapshot = (
                ReviewMeshLedgerSnapshotV1(
                    current.entries
                    + (
                        entry,
                    )
                )
            )

            if (
                next_snapshot.head_digest
                != outcome.ledger.head_digest
            ):
                raise LedgerReconciliationError(
                    "durable/in-memory ledger head disagreement"
                )

            self._atomic_write(
                next_snapshot
            )

            persisted = (
                self._load_unlocked()
            )

            if (
                persisted.head_digest
                != next_snapshot.head_digest
                or persisted.record_count
                != next_snapshot.record_count
            ):
                raise LedgerReconciliationError(
                    "durable ledger post-write verification failed"
                )

            return (
                DurableLedgerAppendOutcomeV1(
                    snapshot=persisted,
                    record=outcome.record,
                    duplicate=False,
                    persisted=True,
                )
            )
