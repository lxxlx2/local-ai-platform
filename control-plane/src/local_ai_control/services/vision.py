"""Owner-private Telegram image ingestion and Qwen3.8 vision execution."""
from __future__ import annotations
from pathlib import Path
import uuid

from local_ai_control.services.multimodal import Attachment,AttachmentValidator,PrivateMediaSpool


class TelegramImageService:
    def __init__(self,provider,*,inbox_root=Path("/Users/jerson/AI/runtime/private-media-inbox"),spool_root=Path("/Users/jerson/AI/runtime/private-media"),max_bytes=20*1024**2,ttl_seconds=24*3600):
        self.provider=provider; self.inbox_root=Path(inbox_root).resolve(); self.inbox_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.validator=AttachmentValidator(max_bytes); self.spool=PrivateMediaSpool(Path(spool_root),ttl_seconds); self.max_bytes=max_bytes

    async def analyze(self,bot,file_ref,*,declared_size,caption="",provider=None):
        if not isinstance(declared_size,int) or declared_size<=0 or declared_size>self.max_bytes: raise ValueError("图片大小超出限制。")
        self.spool.cleanup(); temporary=self.inbox_root/f"{uuid.uuid4().hex}.jpg"
        try:
            await bot.download(file_ref,destination=temporary)
            path=self.validator.validate(Attachment(temporary,"image/jpeg",declared_size))
            ref=self.spool.put(path,".jpg")
            reply=(provider or self.provider).vision(ref.path,caption.strip() or "请简洁描述这张图片的主要内容。")
            if not reply.complete or not reply.text: raise RuntimeError("vision response incomplete")
            return reply.text
        finally:
            temporary.unlink(missing_ok=True)
