import asyncio

import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardRemove

from local_ai_control.bot.app import SUPERVISOR_CONTROL_ACTIONS, home_for, send_rendered_output, send_start_dashboard
from local_ai_control.bot.ui import (
    NAVIGATION_ROUTES, back_for, owner_task_menu, parent_route, workflow_controls, workflow_menu,
)
from local_ai_control.services.authorization import AuthorizationDenied, authorize
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.capabilities import MODEL_NAME, capability_intro, model_identity
from local_ai_control.services.code_quality import check_python_block
from local_ai_control.services.chat import needs_standalone_decorator_examples
from local_ai_control.services.intent import classify_owner_text
from local_ai_control.services.output import TelegramOutputRenderer
from local_ai_control.services.models import model_center_text


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def callback_values(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_bug_ux_001_dashboard_is_inline_compact_and_professional():
    owner = identity_from_telegram(1, "1")
    title, markup = home_for(owner)
    assert title == "本地 AI 控制中心\n\n请选择一个功能："
    assert len(labels(markup)) == 8
    assert not any(any(ord(char) > 0x1F000 for char in label) for label in labels(markup))
    assert labels(markup) == ["AI 对话", "待审批", "私人项目", "私人任务", "文件与媒体", "我的记忆", "系统管理", "设置"]


def test_public_dashboard_is_scoped_and_compact():
    public = identity_from_telegram(2, "1")
    _, markup = home_for(public)
    values = labels(markup)
    assert len(values) == 6
    assert not {"私人项目", "待审批", "系统管理"}.intersection(values)


def test_supervisor_navigation_is_owner_only_and_not_on_main_dashboard():
    owner = identity_from_telegram(1, "1")
    public = identity_from_telegram(2, "1")
    _, home = home_for(owner)
    assert "自动工作流" not in labels(home)
    assert labels(owner_task_menu()) == ["任务预览", "自动工作流", "返回"]
    assert labels(workflow_menu()) == ["刷新状态", "创建安全演示", "返回"]
    authorize(owner, "owner:tasks")
    with pytest.raises(AuthorizationDenied):
        authorize(public, "owner:tasks")


def test_supervisor_control_callbacks_are_bounded_and_structured():
    job_id = "12345678-1234-1234-1234-123456789012"
    for status in ("QUEUED", "RUNNING", "WAITING", "FAILED", "BLOCKED", "COMPLETED"):
        markup = workflow_controls(job_id, status)
        callbacks = callback_values(markup)
        assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)
        assert callbacks[-1] == "menu:workflows"
    assert SUPERVISOR_CONTROL_ACTIONS == {"pause", "resume", "cancel", "retry"}


def test_bug_ux_003_back_navigation_uses_parent_route_registry():
    expected = {
        "owner:file": "menu:media",
        "owner:image": "menu:media",
        "owner:video": "menu:media",
        "public:file": "menu:public_media",
        "public:image": "menu:public_media",
        "public:video": "menu:public_media",
        "owner:model": "menu:system",
        "owner:system": "menu:system",
        "owner:features": "menu:system",
        "owner:reports": "menu:system",
        "public:preview": "menu:system",
    }
    assert {route: parent_route(route) for route in expected} == expected
    assert all(callback_values(back_for(route)) == [parent] for route, parent in expected.items())
    assert all(NAVIGATION_ROUTES[route].render_callback for route in expected)


def test_bug_ux_004_start_cleanup_message_is_deleted_after_dashboard():
    class Sent:
        def __init__(self, text, markup):
            self.text, self.markup, self.deleted = text, markup, False

        async def delete(self):
            self.deleted = True

    class Target:
        def __init__(self):
            self.sent = []

        async def answer(self, text, reply_markup=None):
            message = Sent(text, reply_markup)
            self.sent.append(message)
            return message

    target = Target()
    dashboard_markup = home_for(identity_from_telegram(1, "1"))[1]
    dashboard = asyncio.run(send_start_dashboard(target, "Dashboard", dashboard_markup))
    assert len(target.sent) == 2
    assert isinstance(target.sent[0].markup, ReplyKeyboardRemove) and target.sent[0].deleted
    assert dashboard is target.sent[1] and dashboard.text == "Dashboard"
    assert isinstance(dashboard.markup, InlineKeyboardMarkup)
    assert [message.text for message in target.sent if not message.deleted] == ["Dashboard"]


def test_start_dashboard_delete_failure_does_not_hide_dashboard():
    class Cleanup:
        async def delete(self):
            raise RuntimeError("synthetic delete failure")

    class Dashboard:
        pass

    class Target:
        def __init__(self):
            self.calls = 0

        async def answer(self, _text, reply_markup=None):
            self.calls += 1
            return Cleanup() if self.calls == 1 else Dashboard()

    target = Target()
    result = asyncio.run(send_start_dashboard(target, "Dashboard", home_for(identity_from_telegram(1, "1"))[1]))
    assert isinstance(result, Dashboard) and target.calls == 2


def test_production_sender_uses_html_parse_mode_and_native_code_payload():
    class Message:
        def __init__(self):
            self.calls = []

        async def answer(self, text, parse_mode=None):
            self.calls.append((text, parse_mode))

    message = Message()
    rendered = asyncio.run(send_rendered_output(message, TelegramOutputRenderer(), "使用 `@require_admin`。"))
    assert message.calls == [(rendered.chunks[0], "HTML")]
    assert "<code>@require_admin</code>" in message.calls[0][0]


def test_bug_chat_001_capability_intro_is_product_aware_and_scoped():
    owner = capability_intro(Role.OWNER)
    public = capability_intro(Role.PUBLIC)
    assert MODEL_NAME in owner and "私人项目" in owner and "审批" in owner and "记忆" in owner
    assert "无法访问你的本地文件" not in owner and "通用 AI" not in owner
    assert MODEL_NAME in public
    assert "私人项目" not in public and "审批" not in public and "系统管理" not in public
    assert classify_owner_text(Role.OWNER, "你好，简单介绍一下你现在能帮我做什么。").kind == "CAPABILITY_INTENT"
    assert classify_owner_text(Role.PUBLIC, "你现在用的是什么模型？").kind == "MODEL_IDENTITY_INTENT"


def test_bug_chat_002_model_identity_has_priority_and_is_short():
    assert classify_owner_text(Role.OWNER, "你现在用的是什么模型？").kind == "MODEL_IDENTITY_INTENT"
    answer = model_identity()
    assert MODEL_NAME in answer and "私人项目" not in answer and len(answer.splitlines()) == 3
    assert classify_owner_text(Role.OWNER, "后端是什么概念？").kind == "CHAT_INTENT"
    assert classify_owner_text(Role.OWNER, "你觉得哪个模型适合写代码？").kind == "CHAT_INTENT"
    assert classify_owner_text(Role.OWNER, "你用的什么模型比较适合写代码？").kind == "CHAT_INTENT"
    assert classify_owner_text(Role.OWNER, "你现在用的是什么模型，适合做编程吗？").kind == "CHAT_INTENT"
    assert classify_owner_text(Role.OWNER, "现在是什么模型概念？").kind == "CHAT_INTENT"
    assert classify_owner_text(Role.OWNER, "请问当前后端是什么？").kind == "MODEL_IDENTITY_INTENT"
    assert classify_owner_text(Role.OWNER, "你现在用的是什么后端？").kind == "MODEL_IDENTITY_INTENT"


def test_bug_tg_003_code_fences_are_not_telegram_visible():
    raw = "### 示例\n```python\nimport functools\n\n@functools.wraps(func)\ndef wrapped(*args, **kwargs):\n    return func(*args, **kwargs)\n```"
    package = TelegramOutputRenderer().package(raw)
    rendered = package.canonical_text
    assert "```" not in rendered and "###" not in rendered
    assert package.parse_mode == "HTML"
    assert "<pre><code>" in "".join(package.chunks)
    assert "@functools.wraps(func)" in rendered and "**kwargs" in rendered
    assert not TelegramOutputRenderer().has_visible_markdown_artifacts(rendered, package.protected_ranges)


def test_bug_code_001_complete_examples_are_syntax_checked_and_self_contained():
    valid = "import functools\n\n@functools.wraps(func)\ndef wrapped(*args, **kwargs):\n    return func(*args, **kwargs)\n"
    missing_import = "@functools.wraps(func)\ndef wrapped():\n    pass\n"
    assert check_python_block(valid).standalone_claim_ok
    assert check_python_block(missing_import).issue == "functools import missing"
    assert needs_standalone_decorator_examples("请给出两个完整装饰器示例", "```python\n" + missing_import + "```")


def test_bug_code_001_rejects_documented_string_used_as_numeric():
    inconsistent = '''
def require_level(required_level):
    """required_level (str): required access level."""
    def decorate(func):
        def wrapped(user_level):
            return func(user_level) if user_level >= required_level else None
        return wrapped
    return decorate

@require_level(3)
def publish(user_level):
    return user_level
'''
    result = check_python_block(inconsistent)
    assert result.syntax_valid and not result.standalone_claim_ok
    assert result.issue == "required_level documented as str but used as numeric"
    keyword_variant = inconsistent.replace("@require_level(3)", "@require_level(required_level=3)")
    assert not check_python_block(keyword_variant).standalone_claim_ok


def test_model_center_is_owner_controlled_and_does_not_claim_coding_ready():
    text = model_center_text()
    assert MODEL_NAME in text and "CODING：未通过" in text and "不会自动下载模型" not in text
