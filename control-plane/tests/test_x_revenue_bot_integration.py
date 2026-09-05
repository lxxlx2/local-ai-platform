"""Tests for X Revenue Integration with the Unified Telegram Control Plane."""

import asyncio
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from local_ai_control.config.settings import Settings, secret_values
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.authorization import AuthorizationDenied, authorize
from local_ai_control.services.x_revenue_approval import (
    apply_decision,
    decode_compact_digest,
    deliver_pending_notifications,
    encode_compact_digest,
    get_candidate_by_sha,
    get_pending_count,
    list_pending_approvals,
)


@pytest.fixture
def temp_artifacts_dir():
    d = tempfile.mkdtemp(prefix="test_x_art_")
    art_path = Path(d)
    yield art_path
    shutil.rmtree(d, ignore_errors=True)


def create_sample_artifact(artifacts_dir: Path, run_id: str, candidate_text: str, status: str = "PENDING", delivery_state: str = "ENQUEUED_FOR_BOT", expires_in_hours: int = 24):
    folder = artifacts_dir / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "candidate.txt").write_text(candidate_text, encoding="utf-8")
    sha = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    exp_iso = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=expires_in_hours)).isoformat()

    app_data = {
        "status": status,
        "candidate_sha256": sha,
        "created_at": now_iso,
        "expires_at": exp_iso,
        "decision": status if status != "PENDING" else "PENDING",
        "actor": None,
        "delivery_state": delivery_state,
        "trigger_summary": "Semis +3.37%",
        "external_publish_allowed": False,
        "telegram_message_id": None,
    }
    (folder / "approval.json").write_text(json.dumps(app_data, indent=2), encoding="utf-8")
    return sha, folder


# 1. Existing Bot credential discovery without displaying secrets
def test_1_credential_discovery_without_displaying_secrets():
    settings = Settings.load()
    # Check that settings loaded values without displaying them
    has_token = bool(settings.token and settings.token.strip())
    has_owner = bool(settings.owner_id and settings.owner_id.strip())
    assert has_token is True, "Token must be discovered"
    assert has_owner is True, "Owner ID must be discovered"
    # Never log or expose values in assertions
    assert isinstance(settings.token, str)
    assert isinstance(settings.owner_id, str)


# 2. Existing Bot Owner identity test
def test_2_owner_identity_enforcement():
    settings = Settings.load()
    owner_ctx = identity_from_telegram(int(settings.owner_id), settings.owner_id)
    public_ctx = identity_from_telegram(999999999, settings.owner_id)

    assert owner_ctx.role is Role.OWNER
    assert public_ctx.role is Role.PUBLIC

    # Owner authorized
    authorize(owner_ctx, "owner:approvals")

    # Public denied
    with pytest.raises(AuthorizationDenied):
        authorize(public_ctx, "owner:approvals")


# 3. X candidate enqueue
def test_3_x_candidate_enqueue(temp_artifacts_dir):
    text = "Sep 4 pulse: Nasdaq Comp -0.29%, Semis +3.37%. Breadth 2/3. Read: divergence."
    sha, folder = create_sample_artifact(temp_artifacts_dir, "20260906T000000Z", text, "PENDING", "ENQUEUED_FOR_BOT")

    app_file = folder / "approval.json"
    assert app_file.is_file()
    data = json.loads(app_file.read_text())
    assert data["status"] == "PENDING"
    assert data["delivery_state"] == "ENQUEUED_FOR_BOT"
    assert data["candidate_sha256"] == sha


# 4. Bot sees pending X approval
def test_4_bot_sees_pending_approvals(temp_artifacts_dir):
    text = "Candidate 1 text"
    sha, _ = create_sample_artifact(temp_artifacts_dir, "20260906T010000Z", text, "PENDING")

    pending = list_pending_approvals(temp_artifacts_dir)
    assert len(pending) == 1
    assert pending[0]["candidate_sha256"] == sha
    assert get_pending_count(temp_artifacts_dir) == 1


# 5. Exact SHA approve
def test_5_exact_sha_approve(temp_artifacts_dir):
    text = "Candidate to approve"
    sha, folder = create_sample_artifact(temp_artifacts_dir, "20260906T020000Z", text, "PENDING")

    res = apply_decision(temp_artifacts_dir, sha, "APPROVED", "telegram_owner:12345")
    assert res["decision"] == "APPROVED"
    assert res["candidate_sha256"] == sha

    data = json.loads((folder / "approval.json").read_text())
    assert data["status"] == "APPROVED"
    assert data["decision"] == "APPROVED"
    assert data["external_publish_allowed"] is False
    assert "telegram_owner:12345" in data["actor"]


# 6. Exact SHA reject
def test_6_exact_sha_reject(temp_artifacts_dir):
    text = "Candidate to reject"
    sha, folder = create_sample_artifact(temp_artifacts_dir, "20260906T030000Z", text, "PENDING")

    res = apply_decision(temp_artifacts_dir, sha, "REJECTED", "telegram_owner:12345")
    assert res["decision"] == "REJECTED"

    data = json.loads((folder / "approval.json").read_text())
    assert data["status"] == "REJECTED"
    assert data["decision"] == "REJECTED"
    assert data["external_publish_allowed"] is False


# 7. Public user denied
def test_7_public_user_denied_access():
    settings = Settings.load()
    public_ctx = identity_from_telegram(987654321, settings.owner_id)

    with pytest.raises(AuthorizationDenied):
        authorize(public_ctx, "owner:approvals")


# 8. Stale / replayed callback rejected
def test_8_stale_and_replayed_callbacks_rejected(temp_artifacts_dir):
    text = "Candidate edge cases"
    sha, folder = create_sample_artifact(temp_artifacts_dir, "20260906T040000Z", text, "PENDING")

    # 1. First decision succeeds
    apply_decision(temp_artifacts_dir, sha, "APPROVED", "telegram_owner:123")

    # 2. Replayed callback fails
    with pytest.raises(ValueError, match="ALREADY_FINALIZED"):
        apply_decision(temp_artifacts_dir, sha, "APPROVED", "telegram_owner:123")

    # 3. Expired candidate fails
    text_stale = "Stale candidate"
    sha_stale, _ = create_sample_artifact(temp_artifacts_dir, "20260906T050000Z", text_stale, "PENDING", expires_in_hours=-2)
    with pytest.raises(ValueError, match="STALE_CALLBACK"):
        apply_decision(temp_artifacts_dir, sha_stale, "APPROVED", "telegram_owner:123")


# 9. Duplicate notification suppressed
def test_9_duplicate_notification_suppression(temp_artifacts_dir):
    async def _run():
        text = "Candidate for delivery"
        sha, folder = create_sample_artifact(temp_artifacts_dir, "20260906T060000Z", text, "PENDING", delivery_state="ENQUEUED_FOR_BOT")

        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_msg.message_id = 777
        mock_bot.send_message = AsyncMock(return_value=mock_msg)

        # First delivery succeeds
        delivered = await deliver_pending_notifications(mock_bot, "123456", temp_artifacts_dir)
        assert len(delivered) == 1
        mock_bot.send_message.assert_called_once()

        data = json.loads((folder / "approval.json").read_text())
        assert data["delivery_state"] == "SENT"
        assert data["telegram_message_id"] == 777

        # Second delivery run does NOT re-send
        mock_bot.send_message.reset_mock()
        delivered_2 = await deliver_pending_notifications(mock_bot, "123456", temp_artifacts_dir)
        assert len(delivered_2) == 0
        mock_bot.send_message.assert_not_called()

    asyncio.run(_run())


# 10. Restart / state persistence behavior
def test_10_restart_state_persistence(temp_artifacts_dir):
    text = "Persistent state candidate"
    sha, folder = create_sample_artifact(temp_artifacts_dir, "20260906T070000Z", text, "PENDING")
    apply_decision(temp_artifacts_dir, sha, "APPROVED", "telegram_owner:999")

    # Simulate fresh service instance loading from disk
    item = get_candidate_by_sha(temp_artifacts_dir, sha)
    assert item is not None
    assert item["status"] == "APPROVED"
    assert item["decision"] == "APPROVED"
    assert item["actor"] == "telegram_owner:999"


# 11. Publisher remains disabled
def test_11_publisher_remains_disabled(temp_artifacts_dir):
    text = "Publish locked candidate"
    sha, folder = create_sample_artifact(temp_artifacts_dir, "20260906T080000Z", text, "PENDING")
    apply_decision(temp_artifacts_dir, sha, "APPROVED", "telegram_owner:999")

    data = json.loads((folder / "approval.json").read_text())
    assert data["external_publish_allowed"] is False


# 12. No Telegram token copied into X repository / log / artifacts
def test_12_no_telegram_token_in_x_repo():
    x_root = Path("/Users/jerson/Documents/ChatGPT/全自动化模型/x-revenue")
    settings = Settings.load()
    bot_token = settings.token

    assert bot_token is not None and len(bot_token) > 10

    # Scan X repo files for bot token
    for fpath in x_root.rglob("*"):
        if fpath.is_file() and not fpath.name.endswith((".pyc", ".lock", ".png")):
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            assert bot_token not in content, f"Bot token leaked into X repo file: {fpath}"


def test_candidate_hash_preserves_spaces_and_strips_only_newline(temp_artifacts_dir):
    candidate = "  intentional leading and trailing spaces  "
    folder = temp_artifacts_dir / "hash-contract"
    folder.mkdir()

    raw = (candidate + "\n").encode("utf-8")
    (folder / "candidate.txt").write_bytes(raw)

    sha = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    now = dt.datetime.now(dt.timezone.utc)

    approval = {
        "status": "PENDING",
        "decision": "PENDING",
        "candidate_sha256": sha,
        "created_at": now.isoformat(),
        "expires_at": (now + dt.timedelta(hours=1)).isoformat(),
        "delivery_state": "ENQUEUED_FOR_BOT",
        "external_publish_allowed": False,
    }

    (folder / "approval.json").write_text(
        json.dumps(approval),
        encoding="utf-8",
    )

    items = list_pending_approvals(temp_artifacts_dir)

    assert len(items) == 1
    assert items[0]["candidate_sha256"] == sha
    assert items[0]["candidate_text"] == candidate
