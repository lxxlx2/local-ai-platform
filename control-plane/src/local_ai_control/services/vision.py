"""Owner-private Telegram image ingestion and Qwen3.8 vision execution."""
from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import uuid

from local_ai_control.services.multimodal import Attachment,AttachmentValidator,PrivateMediaSpool


@dataclass(frozen=True)
class VisionRequest:
    path: Path
    prompt: str


class TelegramImageService:
    def __init__(self,provider,*,inbox_root=Path("/Users/jerson/AI/runtime/private-media-inbox"),spool_root=Path("/Users/jerson/AI/runtime/private-media"),max_bytes=20*1024**2,ttl_seconds=24*3600):
        self.provider=provider; self.inbox_root=Path(inbox_root).resolve(); self.inbox_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.inbox_root,0o700)
        self.validator=AttachmentValidator(max_bytes); self.spool=PrivateMediaSpool(Path(spool_root),ttl_seconds); self.max_bytes=max_bytes

    async def stage(self,bot,file_ref,*,declared_size,caption=""):
        """Await Telegram transport before any heavy-runtime admission."""
        if not isinstance(declared_size,int) or declared_size<=0 or declared_size>self.max_bytes: raise ValueError("图片大小超出限制。")
        self.spool.cleanup(); temporary=self.inbox_root/f"{uuid.uuid4().hex}.jpg"
        try:
            descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.close(descriptor)
            await bot.download(file_ref,destination=temporary)
            os.chmod(temporary,0o600)
            path=self.validator.validate(Attachment(temporary,"image/jpeg",declared_size))
            ref=self.spool.put(path,".jpg")
            return VisionRequest(ref.path,caption.strip() or "请简洁描述这张图片的主要内容。")
        finally:
            temporary.unlink(missing_ok=True)

    def infer(self,request,provider=None):
        reply=(provider or self.provider).vision(request.path,request.prompt)
        if not reply.complete or not reply.text: raise RuntimeError("vision response incomplete")
        return reply.text

    def discard(self,request):
        path=Path(request.path)
        try:
            resolved=path.resolve(strict=True)
        except FileNotFoundError:
            return
        if resolved.parent==self.spool.root and resolved.is_file() and not resolved.is_symlink():
            resolved.unlink()
