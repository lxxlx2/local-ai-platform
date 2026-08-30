import json
import os
from pathlib import Path
import stat

import pytest

from local_ai_control.services.review_mesh_ledger import (
    LEDGER_GENESIS_DIGEST,
    LedgerRecordType,
    LedgerReconciliationError,
)

from local_ai_control.services.review_mesh_ledger_store import (
    ReviewMeshLedgerStoreV1,
)


A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64


def store_path(
    tmp_path,
):
    return (
        tmp_path
        / "review-mesh-ledger-v1.json"
    )


def append(
    store,
    *,
    key,
    payload=None,
    expected_head=None,
    created="2026-08-30T06:00:00+00:00",
):
    return store.append(
        record_type=(
            LedgerRecordType.REVIEW_RESULT
        ),

        payload=(
            {
                "value": key,
                "evidence": A,
            }
            if payload is None
            else payload
        ),

        related_task_id="g0a-slice5",

        related_request_id=(
            "rr1:" + A
        ),

        related_campaign_id=(
            "rc1:" + B
        ),

        actor_provenance_digest=C,
        ingestion_receipt_digest=D,

        idempotency_key=key,

        created_at=created,

        expected_head_digest=(
            expected_head
        ),
    )


def test_missing_store_loads_empty_without_creating_file(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    snapshot = store.load()

    assert snapshot.record_count == 0

    assert (
        snapshot.head_digest
        == LEDGER_GENESIS_DIGEST
    )

    assert not path.exists()
    assert not store.lock_path.exists()


def test_first_append_creates_private_snapshot(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    outcome = append(
        store,
        key="record-1",
        expected_head=(
            LEDGER_GENESIS_DIGEST
        ),
    )

    assert outcome.persisted is True
    assert outcome.duplicate is False

    assert path.is_file()

    mode = stat.S_IMODE(
        path.stat().st_mode
    )

    assert mode == 0o600

    assert (
        outcome.snapshot.record_count
        == 1
    )


def test_restart_recovers_exact_head_and_payload(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    first_store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    first = append(
        first_store,
        key="record-1",
    )

    restarted = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    loaded = restarted.load()

    assert (
        loaded.head_digest
        == first.snapshot.head_digest
    )

    assert (
        loaded.record_count
        == 1
    )

    assert (
        loaded.payload_for_record(
            first.record.record_digest
        )
        == {
            "value": "record-1",
            "evidence": A,
        }
    )


def test_multiple_appends_survive_restart_with_chain(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    first = append(
        store,
        key="record-1",
    )

    second = append(
        store,
        key="record-2",
        expected_head=(
            first.snapshot.head_digest
        ),
        created=(
            "2026-08-30T06:01:00+00:00"
        ),
    )

    loaded = (
        ReviewMeshLedgerStoreV1(
            path
        ).load()
    )

    assert loaded.record_count == 2

    assert (
        loaded.head_digest
        == second.record.record_digest
    )

    assert (
        loaded.entries[1]
        .record
        .previous_ledger_head_digest
        == first.record.record_digest
    )

    assert (
        loaded.ledger
        .verify_continuity()
    )


def test_exact_duplicate_replay_does_not_rewrite_store(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    first = append(
        store,
        key="same-delivery",
        payload={"result": A},
    )

    before_bytes = (
        path.read_bytes()
    )

    before_stat = path.stat()

    retry = append(
        store,
        key="same-delivery",
        payload={"result": A},
        created=(
            "2026-08-30T06:05:00+00:00"
        ),
    )

    after_stat = path.stat()

    assert retry.duplicate is True
    assert retry.persisted is False

    assert (
        retry.record
        == first.record
    )

    assert (
        path.read_bytes()
        == before_bytes
    )

    assert (
        after_stat.st_ino
        == before_stat.st_ino
    )


def test_conflicting_idempotency_replay_preserves_old_file(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    first = append(
        store,
        key="same-key",
        payload={"result": A},
    )

    before = path.read_bytes()

    with pytest.raises(
        LedgerReconciliationError,
        match="conflicting ledger idempotency key",
    ):
        append(
            store,
            key="same-key",
            payload={"result": B},
        )

    assert (
        path.read_bytes()
        == before
    )

    assert (
        store.load().head_digest
        == first.snapshot.head_digest
    )


def test_stale_expected_head_blocks_lost_update(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store_a = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    store_b = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    first = append(
        store_a,
        key="record-1",
        expected_head=(
            LEDGER_GENESIS_DIGEST
        ),
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="stale expected ledger head",
    ):
        append(
            store_b,
            key="record-2",
            expected_head=(
                LEDGER_GENESIS_DIGEST
            ),
        )

    assert (
        store_b.load().head_digest
        == first.snapshot.head_digest
    )


def test_tampered_payload_is_detected_on_restart(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    append(
        store,
        key="record-1",
    )

    raw = json.loads(
        path.read_text()
    )

    raw["entries"][0]["payload"][
        "value"
    ] = "tampered"

    path.write_text(
        json.dumps(
            raw,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="payload digest mismatch",
    ):
        ReviewMeshLedgerStoreV1(
            path
        ).load()


def test_tampered_record_digest_is_detected(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    append(
        store,
        key="record-1",
    )

    raw = json.loads(
        path.read_text()
    )

    raw["entries"][0]["record"][
        "record_digest"
    ] = "0" * 64

    path.write_text(
        json.dumps(
            raw,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="record digest mismatch",
    ):
        ReviewMeshLedgerStoreV1(
            path
        ).load()


def test_truncated_snapshot_is_detected(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    append(
        store,
        key="record-1",
    )

    path.write_bytes(
        path.read_bytes()[:40]
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="invalid JSON",
    ):
        ReviewMeshLedgerStoreV1(
            path
        ).load()


def test_record_count_tampering_is_detected(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    append(
        store,
        key="record-1",
    )

    raw = json.loads(
        path.read_text()
    )

    raw["record_count"] = 99

    path.write_text(
        json.dumps(
            raw,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="record count mismatch",
    ):
        ReviewMeshLedgerStoreV1(
            path
        ).load()


def test_head_tampering_is_detected(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    append(
        store,
        key="record-1",
    )

    raw = json.loads(
        path.read_text()
    )

    raw["head_digest"] = (
        "0" * 64
    )

    path.write_text(
        json.dumps(
            raw,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="head digest mismatch",
    ):
        ReviewMeshLedgerStoreV1(
            path
        ).load()


def test_world_or_group_readable_snapshot_is_rejected(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    append(
        store,
        key="record-1",
    )

    os.chmod(
        path,
        0o644,
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="permissions are not owner-private",
    ):
        ReviewMeshLedgerStoreV1(
            path
        ).load()


def test_snapshot_symlink_is_rejected(
    tmp_path,
):
    real = (
        tmp_path
        / "real-ledger.json"
    )

    store = (
        ReviewMeshLedgerStoreV1(
            real
        )
    )

    append(
        store,
        key="record-1",
    )

    link = (
        tmp_path
        / "linked-ledger.json"
    )

    link.symlink_to(
        real
    )

    with pytest.raises(
        LedgerReconciliationError,
        match="symlink",
    ):
        ReviewMeshLedgerStoreV1(
            link
        ).load()


def test_failure_before_replace_preserves_previous_snapshot(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    normal = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    first = append(
        normal,
        key="record-1",
    )

    def fail(stage):
        if stage == "AFTER_TEMP_FSYNC":
            raise RuntimeError(
                "SIMULATED_CRASH_BEFORE_REPLACE"
            )

    crashing = (
        ReviewMeshLedgerStoreV1(
            path,
            fault_hook=fail,
        )
    )

    with pytest.raises(
        RuntimeError,
        match="SIMULATED_CRASH_BEFORE_REPLACE",
    ):
        append(
            crashing,
            key="record-2",
        )

    restarted = (
        ReviewMeshLedgerStoreV1(
            path
        ).load()
    )

    assert restarted.record_count == 1

    assert (
        restarted.head_digest
        == first.snapshot.head_digest
    )


def test_failure_after_replace_leaves_complete_new_snapshot(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    normal = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    append(
        normal,
        key="record-1",
    )

    def fail(stage):
        if stage == "AFTER_REPLACE":
            raise RuntimeError(
                "SIMULATED_CRASH_AFTER_REPLACE"
            )

    crashing = (
        ReviewMeshLedgerStoreV1(
            path,
            fault_hook=fail,
        )
    )

    with pytest.raises(
        RuntimeError,
        match="SIMULATED_CRASH_AFTER_REPLACE",
    ):
        append(
            crashing,
            key="record-2",
            created=(
                "2026-08-30T06:01:00+00:00"
            ),
        )

    restarted = (
        ReviewMeshLedgerStoreV1(
            path
        ).load()
    )

    # Atomic replace already occurred, so simulation observes
    # the complete next snapshot. A real power-loss boundary is
    # old-or-new depending on filesystem durability timing.
    assert restarted.record_count == 2

    assert (
        restarted.ledger
        .verify_continuity()
    )


def test_orphan_temp_file_does_not_corrupt_canonical_snapshot(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    first = append(
        store,
        key="record-1",
    )

    orphan = (
        tmp_path
        / (
            "."
            + path.name
            + ".tmp-orphan"
        )
    )

    orphan.write_bytes(
        b'{"partial":'
    )

    loaded = (
        ReviewMeshLedgerStoreV1(
            path
        ).load()
    )

    assert loaded.record_count == 1

    assert (
        loaded.head_digest
        == first.snapshot.head_digest
    )


def test_payload_lookup_requires_exact_record_digest(
    tmp_path,
):
    path = store_path(
        tmp_path
    )

    store = (
        ReviewMeshLedgerStoreV1(
            path
        )
    )

    append(
        store,
        key="record-1",
    )

    loaded = store.load()

    with pytest.raises(
        KeyError,
        match="payload not found",
    ):
        loaded.payload_for_record(
            "0" * 64
        )
