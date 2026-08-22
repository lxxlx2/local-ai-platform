import pytest
from local_ai_control.domain.identity import Role
from local_ai_control.services.models import ModelRegistry
from local_ai_control.services.multimodal import MultimodalRouter
def test_public_cannot_select_raw_or_generation_capabilities():
    with pytest.raises(PermissionError): ModelRegistry().require("owner-qwen38-raw",owner=False)
    for prompt in ("生成图片","生成视频","生成语音"):
        with pytest.raises(PermissionError): MultimodalRouter().route(Role.PUBLIC,prompt)
def test_public_understanding_does_not_expand_tools():
    route=MultimodalRouter().route(Role.PUBLIC,"解释这个截图","image/png")
    assert route.owner_only is False and route.reason=="image attachment"
