import pytest
from local_ai_control.domain.identity import Role
from local_ai_control.services.models import ModelRole
from local_ai_control.services.multimodal import MediaIntent, MultimodalRouter

def test_attachments_route_to_native_qwen38_roles():
    router=MultimodalRouter()
    assert router.route(Role.OWNER,"这是什么", "image/png").model_role is ModelRole.VISION
    assert router.route(Role.OWNER,"总结", "video/mp4").model_role is ModelRole.VIDEO_UNDERSTANDING
    assert router.route(Role.OWNER,"转写", "audio/mpeg").intent is MediaIntent.STT

def test_generation_is_owner_only_and_public_image_understanding_stays_read_only():
    router=MultimodalRouter()
    assert not router.route(Role.PUBLIC,"解释图片","image/jpeg").owner_only
    with pytest.raises(PermissionError): router.route(Role.PUBLIC,"生成图片：猫")
