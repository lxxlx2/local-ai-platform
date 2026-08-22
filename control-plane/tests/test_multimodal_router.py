import pytest
from pathlib import Path
from local_ai_control.domain.identity import Role
from local_ai_control.services.models import ModelRole
from local_ai_control.services.multimodal import Attachment,AttachmentValidationError,AttachmentValidator,MediaIntent,MultimodalRouter,PrivateMediaSpool

def test_attachments_route_to_native_qwen38_roles():
    router=MultimodalRouter()
    assert router.route(Role.OWNER,"这是什么", "image/png").model_role is ModelRole.VISION
    assert router.route(Role.OWNER,"总结", "video/mp4").model_role is ModelRole.VIDEO_UNDERSTANDING
    assert router.route(Role.OWNER,"转写", "audio/mpeg").intent is MediaIntent.STT

def test_generation_is_owner_only_and_public_image_understanding_stays_read_only():
    router=MultimodalRouter()
    assert not router.route(Role.PUBLIC,"解释图片","image/jpeg").owner_only
    with pytest.raises(PermissionError): router.route(Role.PUBLIC,"生成图片：猫")

def test_attachment_magic_symlink_and_private_spool_ttl(tmp_path):
    image=tmp_path/"a.png"; image.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
    assert AttachmentValidator().validate(Attachment(image,"image/png",image.stat().st_size))==image
    link=tmp_path/"link.png"; link.symlink_to(image)
    with pytest.raises(AttachmentValidationError): AttachmentValidator().validate(Attachment(link,"image/png",image.stat().st_size))
    spool=PrivateMediaSpool(tmp_path/"spool",ttl_seconds=1); ref=spool.put(image,".png")
    assert ref.path.stat().st_mode & 0o777==0o600 and spool.cleanup(now=ref.expires_at+1)==1
