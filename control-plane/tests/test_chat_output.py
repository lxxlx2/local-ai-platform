from dataclasses import replace

import pytest

from local_ai_control.domain.identity import identity_from_telegram
from local_ai_control.services.chat import CHAT_DEFAULT_MAX_OUTPUT_TOKENS, CHAT_LONG_MAX_OUTPUT_TOKENS, ChatService, output_budget
from local_ai_control.services.omlx import ModelReply
from local_ai_control.services.output import CaptureTelegramTransport, TELEGRAM_SAFE_CHUNK_SIZE, TelegramOutputRenderer, chunk_text
from local_ai_control.services.storage import ScopedSQLiteRepository


@pytest.fixture
def identities():
    return identity_from_telegram(1, "1"), identity_from_telegram(2, "1")


@pytest.fixture
def public_repo(tmp_path):
    repo = ScopedSQLiteRepository(tmp_path / "public.db", "public")
    repo.migrate()
    yield repo
    repo.close()


def test_bug_tg_001_markdown_plain_text_but_code_is_preserved():
    raw = "**标题**\n\n这是**加粗文本**。\n\n### 第二部分\n- 项目1\n\n```python\nresult = \"**test**\"\n```\n\n`value = \"**literal**\"`\n{\"pattern\": \"**keep**\"}"
    package = TelegramOutputRenderer().package(raw)
    rendered = package.canonical_text
    assert "**标题**" not in rendered and "### 第二部分" not in rendered
    assert "标题" in rendered and "第二部分" in rendered
    assert 'result = "**test**"' in rendered
    assert 'value = "**literal**"' in rendered and "‹" not in rendered
    assert "```" not in rendered and "`value" not in rendered
    assert '{"pattern": "**keep**"}' in rendered
    assert not TelegramOutputRenderer().has_visible_markdown_artifacts(rendered, package.protected_ranges)


def test_formatter_handles_plain_text_and_markdown_variants():
    renderer = TelegramOutputRenderer()
    text = "# A\n## B\n### C\n__强调__\n**重点**\n- 项目\n普通中文😀"
    rendered = renderer.render(text)
    assert not TelegramOutputRenderer().has_visible_markdown_artifacts(rendered)
    assert "普通中文😀" in rendered


def test_artifact_checker_is_code_aware_without_weakening_prose_policy():
    renderer = TelegramOutputRenderer()
    raw = '''普通说明不应残留格式控制符。

行内代码包括 `*args`、`**kwargs`、`__name__`、`__doc__`、`value = "**literal**"`、`x ** 2` 和 `r"^\\*\\*$"`。

```python
# Python comment
def wrapper(*args, **kwargs):
    return value ** 2, __name__, __doc__, "**literal**"
```'''
    package = renderer.package(raw)
    assert not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)
    for literal in ("*args", "**kwargs", "__name__", "__doc__", '"**literal**"', "x ** 2", "# Python comment"):
        assert literal in package.canonical_text
    assert renderer.has_visible_markdown_artifacts("这里仍有 **裸加粗** 控制符")
    assert renderer.has_visible_markdown_artifacts("### 裸标题")


def test_artifact_checker_rejects_invalid_protected_ranges():
    renderer = TelegramOutputRenderer()
    with pytest.raises(ValueError):
        renderer.has_visible_markdown_artifacts("text", ((0, 99, "inline_code"),))


def test_nested_inline_literals_restore_without_placeholder_or_content_loss():
    renderer = TelegramOutputRenderer()
    for raw in ('result = `value = "**literal**"`', '{"snippet": "`__name__`", "literal": "**keep**"}'):
        package = renderer.package(raw)
        assert "\uE000" not in package.canonical_text and "\uE001" not in package.canonical_text
        assert "__name__" in package.canonical_text or 'value = "**literal**"' in package.canonical_text
        assert not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)
        assert "".join(package.chunks) == package.canonical_text
        if raw.startswith("{"):
            assert package.canonical_text == raw


def test_user_private_use_characters_cannot_collide_with_internal_placeholders():
    renderer = TelegramOutputRenderer()
    literal = "before \uE0000\uE001 after"
    package = renderer.package(literal)
    assert package.canonical_text == literal
    assert "".join(package.chunks) == literal


def test_protected_ranges_reject_unknown_kind_overlap_and_forgery():
    renderer = TelegramOutputRenderer()
    package = renderer.package("`safe` and **visible**")
    span = package.protected_ranges[0]
    with pytest.raises(ValueError):
        renderer.has_visible_markdown_artifacts(package.canonical_text, (replace(span, kind="unknown"),))
    with pytest.raises(ValueError):
        renderer.has_visible_markdown_artifacts(package.canonical_text, (span, span))
    with pytest.raises(ValueError):
        renderer.has_visible_markdown_artifacts("**visible**", ((0, len("**visible**"), "inline_code"),))
    with pytest.raises(ValueError):
        renderer.has_visible_markdown_artifacts(package.canonical_text, (replace(span, end=len(package.canonical_text)),))
    with pytest.raises(ValueError):
        renderer.has_visible_markdown_artifacts(package.canonical_text + " and **visible**", package.protected_ranges)
    for visible in ("    **visible**", "代码：**visible**", 'result = "**visible**"'):
        if visible == 'result = "**visible**"':
            package = renderer.package(visible)
            assert not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)
        else:
            assert renderer.has_visible_markdown_artifacts(visible)
    assert "**visible**" not in renderer.package("result = **visible**").canonical_text


def test_string_literal_detection_does_not_mask_english_contractions_or_possessives():
    renderer = TelegramOutputRenderer()
    prose_cases = (
        "Don't leave **raw** prose in user's answer.",
        'The user said "Do not leave **raw** prose" in the answer.',
        "The user said 'Do not leave **raw** prose' in the answer.",
        'for example, the user said "**raw**" in prose.',
        'return the phrase "**raw**" in your answer.',
        'print the words "**raw**" for the reader.',
        'from the user\'s note: "**raw**" is prose.',
    )
    for raw in prose_cases:
        package = renderer.package(raw)
        assert "**raw**" not in package.canonical_text
        assert not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)
    for raw in ('result = "**literal**"', "value = '**literal**'", 'print("**literal**")'):
        package = renderer.package(raw)
        assert "**literal**" in package.canonical_text
        assert not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)


def test_protected_ranges_cannot_be_replayed_on_another_canonical_response():
    renderer = TelegramOutputRenderer()
    source = renderer.package("`**bad**` safe")
    with pytest.raises(ValueError):
        renderer.has_visible_markdown_artifacts("**bad** prose", source.protected_ranges)
    assert renderer.has_visible_markdown_artifacts("**bad** prose")


def test_unfenced_assignment_keeps_literal_asterisks():
    rendered = TelegramOutputRenderer().render('result = "**test**"')
    assert rendered == 'result = "**test**"'


def test_multiline_json_and_shell_literals_are_preserved():
    raw = '{\n  "pattern": "**keep**",\n  "under": "__keep__"\n}\n\ncurl -H "X-Test: **keep**" https://example.invalid | cat\nexport VALUE="__keep__"'
    rendered = TelegramOutputRenderer().render(raw)
    assert rendered == raw


def test_chunking_reconstructs_without_loss_or_duplication():
    text = ("第一段。\n\n" * 900) + "结尾。"
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= TELEGRAM_SAFE_CHUNK_SIZE for chunk in chunks)
    capture = CaptureTelegramTransport()
    for index, chunk in enumerate(chunks, 1):
        capture.send(chunk, index, len(chunks), parse_mode=None)
    assert capture.reconstructed() == text
    assert [item["chunk_index"] for item in capture.messages] == list(range(1, len(chunks) + 1))
    assert all(item["parse_mode"] is None for item in capture.messages)


def test_chunk_boundaries_and_empty_text():
    for size in (TELEGRAM_SAFE_CHUNK_SIZE - 1, TELEGRAM_SAFE_CHUNK_SIZE, TELEGRAM_SAFE_CHUNK_SIZE + 1, TELEGRAM_SAFE_CHUNK_SIZE * 3 + 9):
        text = "中" * size
        chunks = chunk_text(text)
        assert "".join(chunks) == text
        assert all(len(chunk) <= TELEGRAM_SAFE_CHUNK_SIZE for chunk in chunks)
    assert chunk_text("")[0]


def test_output_budget_is_centralized_and_intent_sensitive():
    assert output_budget("你好，简单介绍一下你能做什么") == CHAT_DEFAULT_MAX_OUTPUT_TOKENS
    assert output_budget("请详细解释 Python 装饰器") == CHAT_LONG_MAX_OUTPUT_TOKENS


class CompleteProvider:
    def generate(self, prompt, max_output_tokens=1024):
        return ModelReply("**简短回答**。", "completed", None, 4, max_output_tokens)


class IncompleteProvider:
    def generate(self, prompt, max_output_tokens=1024):
        return ModelReply("半句话", "incomplete", "max_output_tokens", max_output_tokens, max_output_tokens)


def test_bug_tg_002_incomplete_is_not_persisted(public_repo, identities):
    _, user = identities
    session = public_repo.create_session(user)
    result = ChatService(public_repo, IncompleteProvider()).reply(user, session, "请详细说明")
    assert not result.complete and result.finish_reason == "max_output_tokens"
    assert "尚未完整" in result.text
    messages = public_repo.recent_messages(user, session)
    assert len(messages) == 1 and messages[0]["role"] == "user"


def test_complete_response_is_canonical_and_renderable(public_repo, identities):
    _, user = identities
    session = public_repo.create_session(user)
    result = ChatService(public_repo, CompleteProvider()).reply(user, session, "你好")
    assert result.complete
    assert TelegramOutputRenderer().render(result.text) == "简短回答。"
    assert len(public_repo.recent_messages(user, session)) == 2


def test_user_supplied_assignment_is_preserved_in_code_explanation(public_repo, identities):
    _, user = identities
    session = public_repo.create_session(user)
    result = ChatService(public_repo, CompleteProvider()).reply(user, session, '解释下面代码：\nresult = "**test**"')
    assert 'result = "**test**"' in result.text


class InvalidDecoratorProvider:
    def generate(self, prompt, max_output_tokens=1024):
        return ModelReply("```python\n@functools.wraps(func)\ndef wrapped():\n    return requests.get('x')\n```", "completed", None, 10, max_output_tokens)


def test_invalid_standalone_decorator_blocks_are_replaced(public_repo, identities):
    _, user = identities
    session = public_repo.create_session(user)
    result = ChatService(public_repo, InvalidDecoratorProvider()).reply(user, session, "请给出两个完整装饰器示例")
    assert "requests.get" not in result.text
    assert result.text.count("import functools") == 2
