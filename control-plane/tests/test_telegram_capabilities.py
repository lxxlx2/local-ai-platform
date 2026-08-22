from local_ai_control.bot.ui import media_menu,system_menu
from local_ai_control.domain.identity import Role
from local_ai_control.services.capabilities import routed_capability_text
from local_ai_control.services.multimodal import MultimodalRouter

def labels(keyboard): return [button.text for row in keyboard.inline_keyboard for button in row]
def callbacks(keyboard): return [button.callback_data for row in keyboard.inline_keyboard for button in row]

def test_owner_navigation_exposes_registered_capabilities_without_repo_ids():
    media=media_menu(owner=True); system=system_menu()
    assert {"视觉理解","语音","图片生成","视频理解","视频生成","任务与进度"} <= set(labels(media))
    assert "联网搜索" in labels(system)
    assert all("/" not in value for value in callbacks(media)+callbacks(system))

def test_telegram_route_text_does_not_claim_unqualified_model_ready():
    decision=MultimodalRouter().route(Role.OWNER,"生成图片：海边")
    text=routed_capability_text(decision)
    assert "已注册" in text and "尚未完成本机 qualification" in text and "当前生产 Bot 不会执行" in text
