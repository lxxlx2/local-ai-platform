"""Safe deterministic routing and bounded attachment validation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from local_ai_control.domain.identity import Role
from local_ai_control.services.models import ModelRole


class MediaIntent(StrEnum):
    CHAT="CHAT"; VISION="VISION"; VIDEO_UNDERSTANDING="VIDEO_UNDERSTANDING"
    STT="STT"; TTS="TTS"; IMAGE_GENERATION="IMAGE_GENERATION"; IMAGE_EDIT="IMAGE_EDIT"
    VIDEO_GENERATION="VIDEO_GENERATION"; WEB="WEB"


@dataclass(frozen=True)
class Attachment:
    path: Path; declared_mime: str; size_bytes: int


@dataclass(frozen=True)
class RouteDecision:
    intent: MediaIntent; model_role: ModelRole | None; owner_only: bool; reason: str


class AttachmentValidationError(ValueError): pass


class AttachmentValidator:
    MAGIC={
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/webp": (b"RIFF",),
        "audio/mpeg": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
        "audio/wav": (b"RIFF",),
        "video/mp4": (b"\x00\x00\x00",),
    }
    def __init__(self, max_bytes=50*1024**2): self.max_bytes=max_bytes
    def validate(self, attachment):
        path=attachment.path.resolve(strict=True)
        if path.is_symlink(): raise AttachmentValidationError("symlink denied")
        if attachment.size_bytes<0 or attachment.size_bytes>self.max_bytes or path.stat().st_size!=attachment.size_bytes:
            raise AttachmentValidationError("attachment size invalid")
        signatures=self.MAGIC.get(attachment.declared_mime)
        if not signatures: raise AttachmentValidationError("MIME denied")
        head=path.read_bytes()[:16]
        if not any(head.startswith(sig) for sig in signatures): raise AttachmentValidationError("magic bytes mismatch")
        if attachment.declared_mime=="image/webp" and head[8:12]!=b"WEBP": raise AttachmentValidationError("magic bytes mismatch")
        if attachment.declared_mime in {"audio/wav","video/mp4"}:
            expected=b"WAVE" if attachment.declared_mime=="audio/wav" else b"ftyp"
            if expected not in head: raise AttachmentValidationError("magic bytes mismatch")
        return path


class MultimodalRouter:
    def route(self, role: Role, text: str, mime: str | None=None) -> RouteDecision:
        text=text.strip().lower()
        if mime and mime.startswith("image/"):
            edit=any(w in text for w in ("修改","编辑","换成","remove","edit"))
            if edit:
                self._owner(role); return RouteDecision(MediaIntent.IMAGE_EDIT,ModelRole.IMAGE_MAIN,True,"image edit request")
            return RouteDecision(MediaIntent.VISION,ModelRole.VISION,False,"image attachment")
        if mime and mime.startswith("video/"):
            return RouteDecision(MediaIntent.VIDEO_UNDERSTANDING,ModelRole.VIDEO_UNDERSTANDING,False,"video attachment")
        if mime and mime.startswith("audio/"):
            return RouteDecision(MediaIntent.STT,ModelRole.STT_MAIN,False,"audio attachment")
        if any(w in text for w in ("生成视频","图片转视频")):
            self._owner(role); return RouteDecision(MediaIntent.VIDEO_GENERATION,ModelRole.VIDEO_MAIN,True,"generation request")
        if any(w in text for w in ("生成图片","画一张","画个")):
            self._owner(role); return RouteDecision(MediaIntent.IMAGE_GENERATION,ModelRole.IMAGE_MAIN,True,"generation request")
        if any(w in text for w in ("生成语音","念出来","朗读")):
            self._owner(role); return RouteDecision(MediaIntent.TTS,ModelRole.TTS_MAIN,True,"speech request")
        if any(w in text for w in ("搜索一下","联网查","最新","打开这个链接","http://","https://")):
            return RouteDecision(MediaIntent.WEB,None,False,"web request")
        return RouteDecision(MediaIntent.CHAT,ModelRole.MAIN,False,"text chat")
    @staticmethod
    def _owner(role):
        if role is not Role.OWNER: raise PermissionError("capability is owner-only")
