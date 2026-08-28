"""Restart-safe Owner-only Telegram Media Product Workflow wizard."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from urllib.parse import urlparse
import uuid

from local_ai_control.domain.identity import Role
from local_ai_control.services.media_workflow import (
    CompletionMode,
    IntakeMode,
    SUPPORTED_UPLOADS,
    new_media_workspace,
    utc_now,
)


class WizardStep(StrEnum):
    TASK_NAME = "TASK_NAME"
    SOURCE_MODE = "SOURCE_MODE"
    MATERIALS = "MATERIALS"
    EXECUTION_MODE = "EXECUTION_MODE"
    LANGUAGE = "LANGUAGE"
    VOICE = "VOICE"
    COMPLETION_MODE = "COMPLETION_MODE"
    CONFIRMATION = "CONFIRMATION"
    CREATED = "CREATED"
    SCRIPT_EDIT = "SCRIPT_EDIT"
    OWNER_FACT = "OWNER_FACT"


@dataclass(frozen=True)
class WizardSession:
    owner_id: str
    step: WizardStep
    values: dict
    updated_at: str


class MediaWizardStore:
    def __init__(
        self,
        path: Path | str = "/Users/jerson/AI/runtime/control-plane/media-wizard.db",
    ):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS media_wizard("
            "owner_id TEXT PRIMARY KEY,"
            "step TEXT NOT NULL,"
            "values_json TEXT NOT NULL,"
            "updated_at TEXT NOT NULL)"
        )
        self.db.commit()

    def _save(self, owner_id, step, values):
        with self.lock:
            self.db.execute(
                "INSERT INTO media_wizard VALUES(?,?,?,?) "
                "ON CONFLICT(owner_id) DO UPDATE SET "
                "step=excluded.step,"
                "values_json=excluded.values_json,"
                "updated_at=excluded.updated_at",
                (
                    owner_id,
                    WizardStep(step),
                    json.dumps(values, ensure_ascii=False),
                    utc_now(),
                ),
            )
            self.db.commit()
        return self.get(owner_id)

    def start(self, owner_id):
        return self._save(owner_id, WizardStep.TASK_NAME, {})

    def get(self, owner_id):
        row = self.db.execute(
            "SELECT * FROM media_wizard WHERE owner_id=?",
            (owner_id,),
        ).fetchone()
        if not row:
            return None
        return WizardSession(
            row["owner_id"],
            WizardStep(row["step"]),
            json.loads(row["values_json"]),
            row["updated_at"],
        )

    def update(self, owner_id, step, **values):
        current = self.get(owner_id)
        if current is None:
            raise KeyError("wizard not active")
        merged = {**current.values, **values}
        return self._save(owner_id, step, merged)

    def cancel(self, owner_id):
        with self.lock:
            self.db.execute(
                "DELETE FROM media_wizard WHERE owner_id=?",
                (owner_id,),
            )
            self.db.commit()

    def close(self):
        self.db.close()


class MediaWizardController:
    """Deterministic wizard with durable multi-input collection."""

    MAX_URLS = 10
    MAX_UPLOADS = 10
    MAX_UPLOAD_BYTES = 200 * 1024**2

    def __init__(
        self,
        store: MediaWizardStore,
        *,
        job_root="/Users/jerson/AI/runtime/media-jobs",
        staging_root="/Users/jerson/AI/runtime/media-wizard-files",
    ):
        self.store = store
        self.job_root = Path(job_root)
        self.staging_root = Path(staging_root)

    @staticmethod
    def require_owner(role):
        if role is not Role.OWNER:
            raise PermissionError("owner media wizard only")

    def _owner_staging(self, owner_id: str) -> Path:
        safe = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"local-ai-owner:{owner_id}",
        ).hex
        root = self.staging_root.resolve()
        path = root / safe
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        resolved = path.resolve()
        resolved.relative_to(root)
        return resolved

    def start(self, role, owner_id):
        self.require_owner(role)
        self._clear_staging(owner_id)
        return self.store.start(owner_id)

    def text(self, role, owner_id, text):
        self.require_owner(role)
        session = self.store.get(owner_id)
        if not session:
            raise KeyError("wizard not active")

        value = text.strip()
        if not value or len(value) > 100_000:
            raise ValueError("wizard text invalid")

        if session.step is WizardStep.TASK_NAME:
            if len(value) > 80:
                raise ValueError("task name too long")
            return self.store.update(
                owner_id,
                WizardStep.SOURCE_MODE,
                task_name=value,
            )

        if session.step is not WizardStep.MATERIALS:
            raise ValueError("wizard is not waiting for text")

        mode = session.values.get("source_mode")

        if mode == IntakeMode.DIRECT_BRIEF.value:
            return self.store.update(
                owner_id,
                WizardStep.EXECUTION_MODE,
                brief=value,
            )

        if mode not in {
            IntakeMode.LINKS.value,
            IntakeMode.UPLOADS_AND_LINKS.value,
        }:
            raise ValueError("wizard expects uploaded material")

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("wizard URL invalid")

        urls = list(session.values.get("source_urls", []))

        if value not in urls:
            if len(urls) >= self.MAX_URLS:
                raise ValueError("wizard URL limit reached")
            urls.append(value)

        return self.store.update(
            owner_id,
            WizardStep.MATERIALS,
            source_urls=urls,
        )

    def stage_upload_bytes(
        self,
        role,
        owner_id,
        *,
        filename: str,
        payload: bytes,
    ):
        self.require_owner(role)
        session = self.store.get(owner_id)

        if not session or session.step is not WizardStep.MATERIALS:
            raise ValueError("wizard is not collecting material")

        if session.values.get("source_mode") not in {
            IntakeMode.UPLOADS.value,
            IntakeMode.UPLOADS_AND_LINKS.value,
        }:
            raise ValueError("wizard upload not expected")

        if not payload or len(payload) > self.MAX_UPLOAD_BYTES:
            raise ValueError("wizard upload size invalid")

        suffix = Path(filename or "").suffix.lower()
        if suffix not in SUPPORTED_UPLOADS:
            raise ValueError("wizard upload type unsupported")

        uploads = list(session.values.get("uploads", []))
        if len(uploads) >= self.MAX_UPLOADS:
            raise ValueError("wizard upload limit reached")

        root = self._owner_staging(owner_id)
        staged = root / f"{uuid.uuid4().hex[:16]}{suffix}"
        staged.write_bytes(payload)
        os.chmod(staged, 0o600)

        uploads.append(
            {
                "name": Path(filename).name[:200],
                "path": str(staged),
                "size_bytes": len(payload),
            }
        )

        return self.store.update(
            owner_id,
            WizardStep.MATERIALS,
            uploads=uploads,
        )

    def finish_materials(self, role, owner_id):
        self.require_owner(role)
        session = self.store.get(owner_id)

        if not session or session.step is not WizardStep.MATERIALS:
            raise ValueError("wizard materials not active")

        mode = session.values.get("source_mode")
        uploads = session.values.get("uploads", [])
        urls = session.values.get("source_urls", [])

        if mode == IntakeMode.UPLOADS.value and not uploads:
            raise ValueError("at least one upload required")

        if mode == IntakeMode.LINKS.value and not urls:
            raise ValueError("at least one URL required")

        if mode == IntakeMode.UPLOADS_AND_LINKS.value:
            if not uploads or not urls:
                raise ValueError("upload and URL both required")

        if mode == IntakeMode.DIRECT_BRIEF.value:
            raise ValueError("direct brief completes from text")

        return self.store.update(
            owner_id,
            WizardStep.EXECUTION_MODE,
        )

    def choice(self, role, owner_id, key, value):
        self.require_owner(role)
        session = self.store.get(owner_id)
        if not session:
            raise KeyError("wizard not active")

        expected = {
            "source_mode": (
                WizardStep.SOURCE_MODE,
                WizardStep.MATERIALS,
                {item.value for item in IntakeMode},
            ),
            "execution_mode": (
                WizardStep.EXECUTION_MODE,
                WizardStep.LANGUAGE,
                {"AUTO", "REVIEW_SCRIPT"},
            ),
            "language": (
                WizardStep.LANGUAGE,
                WizardStep.VOICE,
                {"auto", "zh", "en"},
            ),
            "voice": (
                WizardStep.VOICE,
                WizardStep.COMPLETION_MODE,
                {
                    "auto",
                    "zh-male-25-default",
                    "en-male-25-default",
                },
            ),
            "completion_mode": (
                WizardStep.COMPLETION_MODE,
                WizardStep.CONFIRMATION,
                {item.value for item in CompletionMode},
            ),
        }

        current, next_step, allowed = expected[key]

        if session.step is not current or value not in allowed:
            raise ValueError("wizard choice invalid")

        return self.store.update(
            owner_id,
            next_step,
            **{key: value},
        )

    def confirm(self, role, owner_id):
        self.require_owner(role)
        session = self.store.get(owner_id)

        if not session or session.step is not WizardStep.CONFIRMATION:
            raise ValueError("wizard not ready")

        values = session.values

        required = {
            "task_name",
            "source_mode",
            "execution_mode",
            "language",
            "voice",
            "completion_mode",
        }

        if not required <= set(values):
            raise ValueError("wizard fields incomplete")

        mode = values["source_mode"]

        if mode == IntakeMode.DIRECT_BRIEF.value:
            if not values.get("brief"):
                raise ValueError("wizard brief missing")

        if mode == IntakeMode.UPLOADS.value:
            if not values.get("uploads"):
                raise ValueError("wizard uploads missing")

        if mode == IntakeMode.LINKS.value:
            if not values.get("source_urls"):
                raise ValueError("wizard URLs missing")

        if mode == IntakeMode.UPLOADS_AND_LINKS.value:
            if not values.get("uploads") or not values.get("source_urls"):
                raise ValueError("wizard mixed inputs incomplete")

        workspace = new_media_workspace(
            values["task_name"],
            owner_id,
            intake_mode=values["source_mode"],
            completion_mode=values["completion_mode"],
            root=self.job_root,
        )

        staged_uploads = []

        for upload in values.get("uploads", []):
            source = Path(upload["path"])
            if not source.is_file() or source.is_symlink():
                raise ValueError("wizard staged upload missing")
            staged_uploads.append(workspace.stage_upload(source))

        upload_records = []

        for original, staged in zip(
            values.get("uploads", []),
            staged_uploads,
            strict=True,
        ):
            upload_records.append(
                {
                    "name": original["name"],
                    "path": staged["path"],
                    "size_bytes": staged["size_bytes"],
                }
            )

        workspace.write_artifact(
            "metadata/request.json",
            {
                "schema_version": "0.2",
                "task_name": values["task_name"],
                "source_mode": values["source_mode"],
                "execution_mode": values["execution_mode"],
                "language": values["language"],
                "voice": values["voice"],
                "completion_mode": values["completion_mode"],
                "uploads": upload_records,
                "source_urls": values.get("source_urls", []),
            },
        )

        if values.get("source_urls"):
            workspace.write_artifact(
                "source/links.json",
                {
                    "schema_version": "0.2",
                    "urls": values["source_urls"],
                },
            )

        if values.get("brief"):
            workspace.write_artifact(
                "source/direct-brief.txt",
                values["brief"].strip() + "\n",
            )

        created = self.store.update(
            owner_id,
            WizardStep.CREATED,
            job_ref=workspace.path.name,
            upload_names=[
                item["name"]
                for item in values.get("uploads", [])
            ],
            staged_uploads=staged_uploads,
        )

        self._clear_staging(owner_id)

        return created

    def cancel(self, role, owner_id):
        self.require_owner(role)
        self._clear_staging(owner_id)
        self.store.cancel(owner_id)

    def _clear_staging(self, owner_id):
        root = self._owner_staging(owner_id)
        if root.is_dir():
            shutil.rmtree(root)
        root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)


def material_summary(session: WizardSession) -> str:
    values = session.values
    uploads = values.get("uploads", [])
    urls = values.get("source_urls", [])

    lines = ["已收到："]

    for index, item in enumerate(uploads, 1):
        lines.append(f"{index}. {item.get('name', '文件')}")

    if urls:
        if uploads:
            lines.append("")
        lines.append(f"链接：{len(urls)} 个")

    if not uploads and not urls:
        lines.append("暂无材料")

    return "\n".join(lines)


def wizard_summary(session: WizardSession) -> str:
    values = session.values

    sources = {
        "UPLOADS": "上传材料",
        "LINKS": "链接",
        "UPLOADS_AND_LINKS": "上传材料与链接",
        "DIRECT_BRIEF": "直接说明",
    }

    execution = {
        "AUTO": "自动完成",
        "REVIEW_SCRIPT": "先审文稿",
    }

    completion = {
        "AUTO_COMPLETE": "完成后审视频",
        "SCRIPT_REVIEW_FIRST": "先审文稿再生成",
    }

    voice = {
        "auto": "自动选择",
        "zh-male-25-default": "中文男声 25",
        "en-male-25-default": "English Male 25",
    }

    return (
        "请确认视频任务\n\n"
        f"名称：{values.get('task_name', '未设置')}\n"
        f"材料：{sources.get(values.get('source_mode'), '未设置')}\n"
        f"执行：{execution.get(values.get('execution_mode'), '未设置')}\n"
        f"语言：{values.get('language', 'auto')}\n"
        f"声音：{voice.get(values.get('voice'), '自动选择')}\n"
        f"完成方式：{completion.get(values.get('completion_mode'), '未设置')}"
    )
