from local_ai_control.bot.app import home_for
from local_ai_control.domain.identity import Role, identity_from_telegram
from local_ai_control.services.capabilities import MODEL_NAME, capability_intro
from local_ai_control.services.code_quality import check_python_block
from local_ai_control.services.chat import needs_standalone_decorator_examples
from local_ai_control.services.intent import classify_owner_text
from local_ai_control.services.output import TelegramOutputRenderer
from local_ai_control.services.models import model_center_text


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


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


def test_bug_chat_001_capability_intro_is_product_aware_and_scoped():
    owner = capability_intro(Role.OWNER)
    public = capability_intro(Role.PUBLIC)
    assert MODEL_NAME in owner and "私人项目" in owner and "审批" in owner and "记忆" in owner
    assert "无法访问你的本地文件" not in owner and "通用 AI" not in owner
    assert MODEL_NAME in public
    assert "私人项目" not in public and "审批" not in public and "系统管理" not in public
    assert classify_owner_text(Role.OWNER, "你好，简单介绍一下你现在能帮我做什么。").kind == "CAPABILITY_INTENT"
    assert classify_owner_text(Role.PUBLIC, "你现在用的是什么模型？").kind == "CAPABILITY_INTENT"


def test_bug_tg_003_code_fences_are_not_telegram_visible():
    raw = "### 示例\n```python\nimport functools\n\n@functools.wraps(func)\ndef wrapped(*args, **kwargs):\n    return func(*args, **kwargs)\n```"
    rendered = TelegramOutputRenderer().render(raw)
    assert "```" not in rendered and "###" not in rendered
    assert "代码（python）：" in rendered
    assert "@functools.wraps(func)" in rendered and "**kwargs" in rendered
    assert not TelegramOutputRenderer().has_visible_markdown_artifacts(rendered)


def test_bug_code_001_complete_examples_are_syntax_checked_and_self_contained():
    valid = "import functools\n\n@functools.wraps(func)\ndef wrapped(*args, **kwargs):\n    return func(*args, **kwargs)\n"
    missing_import = "@functools.wraps(func)\ndef wrapped():\n    pass\n"
    assert check_python_block(valid).standalone_claim_ok
    assert check_python_block(missing_import).issue == "functools import missing"
    assert needs_standalone_decorator_examples("请给出两个完整装饰器示例", "```python\n" + missing_import + "```")


def test_model_center_is_owner_controlled_and_does_not_claim_coding_ready():
    text = model_center_text()
    assert MODEL_NAME in text and "CODING：未通过" in text and "不会自动下载模型" not in text
