"""X Revenue Content Approval Service for the Unified Telegram Control Plane."""

from __future__ import annotations

import base64
import datetime as dt
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import re
import uuid
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

LOGGER = logging.getLogger(__name__)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def encode_compact_digest(sha256_hex: str) -> str:
    """Encode 64-char hex SHA256 into <= 43 char URL-safe Base64 string."""
    raw = bytes.fromhex(sha256_hex)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_compact_digest(compact: str) -> str:
    """Decode 43-char URL-safe Base64 string back to 64-char hex SHA256."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", compact):
        raise ValueError("MALFORMED_COMPACT_DIGEST")
    padded = compact + "="
    raw = base64.urlsafe_b64decode(padded)
    if len(raw) != 32:
        raise ValueError("INVALID_DIGEST_LENGTH")
    return raw.hex()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def find_artifact_by_sha(artifacts_path: Path, sha256_hex: str) -> Path | None:
    if not artifacts_path.is_dir():
        return None
    for child in artifacts_path.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            app_file = child / "approval.json"
            if app_file.is_file():
                try:
                    data = json.loads(app_file.read_text(encoding="utf-8"))
                    if data.get("candidate_sha256") == sha256_hex:
                        return child
                except Exception:
                    continue
    return None


def list_pending_approvals(artifacts_path: Path) -> list[dict[str, Any]]:
    """Scan artifact directory for actionable pending candidates, auto-expiring stale ones."""
    pending: list[dict[str, Any]] = []
    if not artifacts_path.is_dir():
        return pending

    now = utc_now()

    for child in sorted(artifacts_path.iterdir(), reverse=True):
        if not child.is_dir() or child.name.startswith("."):
            continue
        app_file = child / "approval.json"
        cand_file = child / "candidate.txt"
        if not app_file.is_file() or not cand_file.is_file():
            continue

        try:
            app_data = json.loads(app_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        status = app_data.get("status")
        if status not in ("PENDING", "PENDING_HUMAN_APPROVAL"):
            continue

        sha = app_data.get("candidate_sha256", "")
        candidate_bytes = cand_file.read_bytes().rstrip(b"\n")
        raw_text = candidate_bytes.decode("utf-8")
        actual_sha = hashlib.sha256(candidate_bytes).hexdigest()
        if actual_sha != sha:
            LOGGER.warning("Candidate hash mismatch in artifact=%s", child.name)
            continue

        # Expiration check
        exp_raw = app_data.get("expires_at")
        if exp_raw:
            try:
                exp_dt = dt.datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
                if now >= exp_dt:
                    app_data["status"] = "EXPIRED"
                    app_data["decision"] = "EXPIRED"
                    app_data["decided_at"] = utc_now_iso()
                    _atomic_write_json(app_file, app_data)
                    continue
            except Exception:
                pass

        pending.append({
            "run_id": child.name,
            "artifact_dir": str(child),
            "candidate_sha256": sha,
            "sha_compact": encode_compact_digest(sha),
            "sha_display": sha[:12],
            "candidate_text": raw_text,
            "trigger_summary": app_data.get("trigger_summary", "无触发原因"),
            "created_at": app_data.get("created_at", ""),
            "expires_at": app_data.get("expires_at", ""),
            "delivery_state": app_data.get("delivery_state", "NOT_SENT"),
            "telegram_message_id": app_data.get("telegram_message_id"),
        })

    # Sort newest first
    pending.sort(key=lambda x: x["created_at"], reverse=True)
    return pending


def get_pending_count(artifacts_path: Path) -> int:
    return len(list_pending_approvals(artifacts_path))


def get_candidate_by_sha(artifacts_path: Path, sha256_hex: str) -> dict[str, Any] | None:
    folder = find_artifact_by_sha(artifacts_path, sha256_hex)
    if not folder:
        return None
    cand_file = folder / "candidate.txt"
    app_file = folder / "approval.json"
    if not cand_file.is_file() or not app_file.is_file():
        return None
    candidate_bytes = cand_file.read_bytes().rstrip(b"\n")
    raw_text = candidate_bytes.decode("utf-8")
    app_data = json.loads(app_file.read_text(encoding="utf-8"))
    return {
        "run_id": folder.name,
        "artifact_dir": str(folder),
        "candidate_sha256": sha256_hex,
        "sha_compact": encode_compact_digest(sha256_hex),
        "sha_display": sha256_hex[:12],
        "candidate_text": raw_text,
        "status": app_data.get("status"),
        "trigger_summary": app_data.get("trigger_summary", "无"),
        "created_at": app_data.get("created_at", ""),
        "expires_at": app_data.get("expires_at", ""),
        "decision": app_data.get("decision"),
        "actor": app_data.get("actor"),
        "decided_at": app_data.get("decided_at"),
    }


def apply_decision(
    artifacts_path: Path,
    sha256_hex: str,
    decision: str,
    actor: str,
) -> dict[str, Any]:
    """Atomically record an approval or rejection decision from the unified Bot."""
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError(f"INVALID_DECISION: {decision}")

    folder = find_artifact_by_sha(artifacts_path, sha256_hex)
    if not folder:
        raise ValueError("UNKNOWN_CANDIDATE")

    cand_file = folder / "candidate.txt"
    app_file = folder / "approval.json"
    lock_file = folder / "approval.lock"

    if not cand_file.is_file() or not app_file.is_file():
        raise ValueError("INCOMPLETE_ARTIFACT")

    # Hash verification
    candidate_bytes = cand_file.read_bytes().rstrip(b"\n")
    if hashlib.sha256(candidate_bytes).hexdigest() != sha256_hex:
        raise ValueError("CANDIDATE_DIGEST_MISMATCH")

    with lock_file.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("CONCURRENT_DECISION_ACTIVE")

        try:
            app_data = json.loads(app_file.read_text(encoding="utf-8"))
            status = app_data.get("status")
            if status not in ("PENDING", "PENDING_HUMAN_APPROVAL"):
                raise ValueError("ALREADY_FINALIZED")

            # Check expiration
            exp_raw = app_data.get("expires_at")
            if exp_raw:
                exp_dt = dt.datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
                if utc_now() >= exp_dt:
                    app_data["status"] = "EXPIRED"
                    app_data["decision"] = "EXPIRED"
                    app_data["decided_at"] = utc_now_iso()
                    _atomic_write_json(app_file, app_data)
                    raise ValueError("STALE_CALLBACK")

            # Apply
            app_data["status"] = decision
            app_data["decision"] = decision
            app_data["decided_at"] = utc_now_iso()
            app_data["actor"] = actor
            app_data["note"] = f"Decided via @Jersonliu_bot control plane by {actor}"
            app_data["external_publish_allowed"] = False
            _atomic_write_json(app_file, app_data)

            return {
                "decision": decision,
                "candidate_sha256": sha256_hex,
                "actor": actor,
                "decided_at": app_data["decided_at"],
                "artifact_dir": str(folder),
            }
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def format_approval_card(item: dict[str, Any]) -> str:
    created = item.get("created_at", "")[:19].replace("T", " ")
    expires = item.get("expires_at", "")[:19].replace("T", " ")
    return (
        "X 内容审批\n\n"
        f"候选推文：\n"
        f"{item['candidate_text']}\n\n"
        f"触发原因：{item.get('trigger_summary', '常规指标')}\n"
        f"候选哈希：{item.get('sha_display', item['candidate_sha256'][:12])}…\n"
        f"创建时间：{created} UTC\n"
        f"过期时间：{expires} UTC\n"
        "发布状态：外部发布已锁定（OFF）"
    )


async def deliver_pending_notifications(bot: Any, owner_id: str, artifacts_path: Path) -> list[dict[str, Any]]:
    """Send approval notification to Owner for newly enqueued candidates, preventing duplicate sends."""
    if not owner_id or not artifacts_path.is_dir():
        return []

    delivered: list[dict[str, Any]] = []
    pending = list_pending_approvals(artifacts_path)

    for item in pending:
        # Check if already delivered
        if item.get("telegram_message_id") or item.get("delivery_state") == "SENT":
            continue

        folder = Path(item["artifact_dir"])
        app_file = folder / "approval.json"
        lock_file = folder / "approval.lock"

        with lock_file.open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue

            try:
                app_data = json.loads(app_file.read_text(encoding="utf-8"))
                if app_data.get("telegram_message_id") or app_data.get("delivery_state") == "SENT":
                    continue

                card_text = (
                    "【X 内容审批提醒】\n\n"
                    f"{format_approval_card(item)}"
                )
                markup = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ 批准", callback_data=f"xapp:A:{item['sha_compact']}"),
                        InlineKeyboardButton(text="❌ 拒绝", callback_data=f"xapp:R:{item['sha_compact']}"),
                    ]
                ])

                msg = await bot.send_message(
                    chat_id=int(owner_id),
                    text=card_text,
                    reply_markup=markup,
                )

                app_data["delivery_state"] = "SENT"
                app_data["telegram_message_id"] = msg.message_id
                app_data["telegram_chat_id"] = str(owner_id)
                _atomic_write_json(app_file, app_data)
                delivered.append(item)
                LOGGER.info("Delivered X approval notification for sha=%s msg_id=%s", item["candidate_sha256"][:12], msg.message_id)
            except Exception as exc:
                LOGGER.warning("Failed to send X approval notification sha=%s err=%s", item["candidate_sha256"][:12], type(exc).__name__)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return delivered
