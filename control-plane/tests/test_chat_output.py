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
    rendered = TelegramOutputRenderer().render(raw)
    assert "**标题**" not in rendered and "### 第二部分" not in rendered
    assert "标题" in rendered and "第二部分" in rendered
    assert 'result = "**test**"' in rendered
    assert '〔代码：value = "**literal**"〕' in rendered
    assert "```" not in rendered and "`value" not in rendered
    assert '{"pattern": "**keep**"}' in rendered
    assert not TelegramOutputRenderer().has_visible_markdown_artifacts(rendered)


def test_formatter_handles_plain_text_and_markdown_variants():
    renderer = TelegramOutputRenderer()
    text = "# A\n## B\n### C\n__强调__\n**重点**\n- 项目\n普通中文😀"
    rendered = renderer.render(text)
    assert not TelegramOutputRenderer().has_visible_markdown_artifacts(rendered)
    assert "普通中文😀" in rendered


def test_unfenced_assignment_keeps_literal_asterisks():
    rendered = TelegramOutputRenderer().render('result = "**test**"')
    assert rendered == 'result = "**test**"'


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
