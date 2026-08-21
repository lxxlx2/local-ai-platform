from dataclasses import replace

import pytest

from local_ai_control.domain.identity import identity_from_telegram
from local_ai_control.services.chat import CHAT_DEFAULT_MAX_OUTPUT_TOKENS, CHAT_LONG_MAX_OUTPUT_TOKENS, ChatService, apply_runnable_claim_policy, output_budget
from local_ai_control.services.code_quality import CodeValidationLevel, GOLDEN_CODE_002_SOURCE, GoldenFixtureSandboxedCodeValidator, check_python_block
from local_ai_control.services.omlx import ModelReply, extract_text
from local_ai_control.services.output import CaptureTelegramTransport, TELEGRAM_SAFE_CHUNK_SIZE, TelegramOutputRenderer, chunk_text, is_safe_telegram_html, telegram_html_to_text
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
        assert "".join(package.visible_chunks) == package.canonical_text
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


def test_native_html_code_rendering_is_safe_and_reconstructs_exactly():
    renderer = TelegramOutputRenderer()
    raw = '''### 示例
使用 `@require_admin`、`*args`、`**kwargs`、`__name__`。

```python
@require_admin
def compare(left, right):
    return left < right and "A&B" != "<script>"
```

<script>alert("x")</script>'''
    package = renderer.package(raw)
    payload = "".join(package.chunks)
    assert package.parse_mode == "HTML"
    assert "<code>@require_admin</code>" in payload
    assert "<pre><code>" in payload and "@require_admin" in payload
    assert "&lt;script&gt;" in payload and "<script>" not in payload
    assert all(is_safe_telegram_html(chunk) for chunk in package.chunks)
    assert telegram_html_to_text(payload) == package.canonical_text
    assert "".join(package.visible_chunks) == package.canonical_text


def test_markdown_bullets_and_horizontal_rules_are_normalized_only_in_prose():
    renderer = TelegramOutputRenderer()
    raw = "* 第一项\n- 第二项\n\n---\n\n```python\nvalue = a * b\nseparator = '---'\n```"
    package = renderer.package(raw)
    assert package.canonical_text.startswith("• 第一项\n• 第二项")
    assert "\n---\n" not in package.canonical_text
    assert "value = a * b" in package.canonical_text and "separator = '---'" in package.canonical_text
    assert not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)
    assert package.canonical_text == telegram_html_to_text("".join(package.chunks))


def test_long_code_chunks_keep_balanced_native_entities_without_loss():
    source = "\n".join(f"value_{index} = {index}  # @decorator **kwargs <tag>&" for index in range(500))
    package = TelegramOutputRenderer().package(f"```python\n{source}\n```")
    assert len(package.chunks) > 1
    assert all(is_safe_telegram_html(chunk) for chunk in package.chunks)
    capture = CaptureTelegramTransport()
    for index, chunk in enumerate(package.chunks, 1):
        capture.send(chunk, index, len(package.chunks), parse_mode=package.parse_mode)
    assert capture.reconstructed() == package.canonical_text
    assert len(capture.reconstructed()) == len(package.canonical_text)


def test_bug_code_002_detects_forwarded_control_keyword_and_validates_golden_fixture():
    broken = '''
def require_admin(func):
    def wrapper(*args, **kwargs):
        current_user = kwargs.get("current_user", "guest")
        if current_user != "admin":
            raise PermissionError
        return func(*args, **kwargs)
    return wrapper
'''
    check = check_python_block(broken)
    assert not check.standalone_claim_ok
    assert check.issue == "control keyword forwarded to wrapped function: current_user"
    validator = GoldenFixtureSandboxedCodeValidator()
    result = validator.validate_python(GOLDEN_CODE_002_SOURCE, "GOLDEN-CODE-002")
    assert result.level is CodeValidationLevel.SANDBOX_EXECUTION_VALIDATED
    assert result.stdout_marker_seen
    unknown = validator.validate_python("print('safe')")
    assert unknown.level is CodeValidationLevel.STATIC_VALIDATED
    assert "non-Golden" in unknown.issue
    subscript_broken = broken.replace('kwargs.get("current_user", "guest")', 'kwargs["current_user"]')
    assert not check_python_block(subscript_broken).standalone_claim_ok
    alias_broken = broken.replace("return func(*args, **kwargs)", "forwarded = kwargs\n        return func(*args, **forwarded)")
    assert not check_python_block(alias_broken).standalone_claim_ok
    for expression in ("kwargs.copy()", "dict(kwargs)", "{**kwargs}"):
        copied_broken = broken.replace(
            "return func(*args, **kwargs)",
            f"forwarded = {expression}\n        return func(*args, **forwarded)",
        )
        copied_check = check_python_block(copied_broken)
        assert not copied_check.standalone_claim_ok
        assert copied_check.issue == "control keyword forwarded to wrapped function: current_user"
        copy_before_consume = broken.replace(
            'current_user = kwargs.get("current_user", "guest")',
            f'current_user = kwargs.get("current_user", "guest")\n        forwarded = {expression}\n        kwargs.pop("current_user", None)',
        ).replace("return func(*args, **kwargs)", "return func(*args, **forwarded)")
        assert not check_python_block(copy_before_consume).standalone_claim_ok
        copy_after_consume = broken.replace(
            'current_user = kwargs.get("current_user", "guest")',
            f'current_user = kwargs.pop("current_user", "guest")\n        forwarded = {expression}',
        ).replace("return func(*args, **kwargs)", "return func(*args, **forwarded)")
        assert check_python_block(copy_after_consume).standalone_claim_ok
    conditional_consume = '''
def decorate(func):
    def wrapped(ok, *args, **kwargs):
        user = kwargs.get("token")
        if ok:
            kwargs.pop("token", None)
        return func(*args, **kwargs)
    return wrapped
'''
    conditional_check = check_python_block(conditional_consume)
    assert not conditional_check.standalone_claim_ok
    assert conditional_check.issue == "control keyword forwarded to wrapped function: token"
    branch_snapshot = '''
def decorate(func):
    def wrapped(ok, *args, **kwargs):
        KEY = "token"
        user = kwargs.get(KEY)
        forwarded = kwargs.copy()
        if ok:
            forwarded = kwargs
        kwargs.pop(KEY, None)
        return func(*args, **forwarded)
    return wrapped
'''
    assert not check_python_block(branch_snapshot).standalone_claim_ok
    safe_rebind = '''
def decorate(func):
    def wrapped(*args, **kwargs):
        user = kwargs.get("token")
        kwargs = {"safe": 1}
        return func(*args, **kwargs)
    return wrapped
'''
    assert check_python_block(safe_rebind).standalone_claim_ok
    for expression in ('kwargs if ok else {"safe": 1}', 'kwargs.copy() if ok else {"safe": 1}', 'kwargs.copy() if ok else kwargs'):
        conditional_mapping = broken.replace(
            'current_user = kwargs.get("current_user", "guest")',
            f'current_user = kwargs.get("current_user", "guest")\n        forwarded = {expression}',
        ).replace("return func(*args, **kwargs)", "return func(*args, **forwarded)")
        assert not check_python_block(conditional_mapping).standalone_claim_ok
    for expression in ("kwargs | {}", "{} | kwargs", "kwargs.copy() | {}", "{**kwargs.copy()}"):
        merged_mapping = broken.replace(
            'current_user = kwargs.get("current_user", "guest")',
            f'current_user = kwargs.get("current_user", "guest")\n        forwarded = {expression}',
        ).replace("return func(*args, **kwargs)", "return func(*args, **forwarded)")
        assert not check_python_block(merged_mapping).standalone_claim_ok
    for expression in ("dict(kwargs.copy())", "dict(kwargs | {})", "dict({**kwargs})", "(kwargs | {}).copy()"):
        nested_mapping = broken.replace(
            'current_user = kwargs.get("current_user", "guest")',
            f'current_user = kwargs.get("current_user", "guest")\n        forwarded = {expression}',
        ).replace("return func(*args, **kwargs)", "return func(*args, **forwarded)")
        assert not check_python_block(nested_mapping).standalone_claim_ok
        direct_nested = broken.replace("return func(*args, **kwargs)", f"return func(*args, **{expression})")
        assert not check_python_block(direct_nested).standalone_claim_ok
    for expression in ("kwargs.copy()", "kwargs | {}", "{**kwargs}"):
        direct_mapping = broken.replace("return func(*args, **kwargs)", f"return func(*args, **{expression})")
        assert not check_python_block(direct_mapping).standalone_claim_ok
    symbolic_reassignment = '''
def decorate(func):
    def wrapped(*args, **kwargs):
        KEY = "token"
        user = kwargs.get(KEY)
        KEY = "other"
        kwargs.pop(KEY, None)
        return func(*args, **kwargs)
    return wrapped
'''
    assert not check_python_block(symbolic_reassignment).standalone_claim_ok
    dynamic_key_source = '''
def decorate(func):
    def wrapped(key, *args, **kwargs):
        user = kwargs.get(key)
        return func(*args, **kwargs)
    return wrapped
'''
    dynamic_check = check_python_block(dynamic_key_source)
    assert not dynamic_check.standalone_claim_ok
    assert dynamic_check.issue == "control keyword forwarded to wrapped function: dynamic"
    for key_expression, prefix in ((('"token"'), ""), ("KEY", 'KEY = "token"\n        '), ("key", "")):
        snapshot_before_pop = f'''
def decorate(func):
    def wrapped(key=None, *args, **kwargs):
        {prefix}forwarded = kwargs.copy()
        user = kwargs.pop({key_expression}, None)
        return func(*args, **forwarded)
    return wrapped
'''
        assert not check_python_block(snapshot_before_pop).standalone_claim_ok
        pop_before_snapshot = f'''
def decorate(func):
    def wrapped(key=None, *args, **kwargs):
        {prefix}user = kwargs.pop({key_expression}, None)
        forwarded = kwargs.copy()
        return func(*args, **forwarded)
    return wrapped
'''
        assert check_python_block(pop_before_snapshot).standalone_claim_ok
    dynamic_reassignment = '''
def decorate(func):
    def wrapped(key, replacement, *args, **kwargs):
        user = kwargs.get(key)
        key = replacement
        kwargs.pop(key, None)
        return func(*args, **kwargs)
    return wrapped
'''
    assert not check_python_block(dynamic_reassignment).standalone_claim_ok
    consumed = broken.replace('kwargs.get("current_user", "guest")', 'kwargs.pop("current_user", "guest")')
    assert check_python_block(consumed).standalone_claim_ok
    deleted = broken.replace(
        'current_user = kwargs.get("current_user", "guest")',
        'current_user = kwargs["current_user"]\n        del kwargs["current_user"]',
    )
    assert check_python_block(deleted).standalone_claim_ok


def test_runnable_claim_policy_never_claims_unknown_code_was_executed():
    answer = "完整代码示例，可直接运行：\n```python\nprint('ok')\n```"
    normalized, level = apply_runnable_claim_policy(answer)
    assert level is CodeValidationLevel.STATIC_VALIDATED
    assert "可直接运行" not in normalized
    assert "STATIC_VALIDATED" in normalized and "未执行" in normalized


def test_runnable_claim_policy_covers_broad_claims_but_preserves_code_literals():
    claims = (
        "已经测试", "可以运行", "运行验证通过", "经验证可运行", "测试通过",
        "已经运行成功", "执行成功", "经过测试",
    )
    for claim in claims:
        answer = f"下面是{claim}的代码：\n```python\nmessage = '{claim}'\nprint(message)\n```"
        normalized, level = apply_runnable_claim_policy(answer)
        assert level is CodeValidationLevel.STATIC_VALIDATED
        assert f"message = '{claim}'" in normalized
        assert claim not in normalized.split("```", 1)[0]
        assert "未执行" in normalized
    inline = "字面量 `已经测试` 不代表声明。\n```python\nprint('ok')\n```"
    normalized, level = apply_runnable_claim_policy(inline)
    assert normalized == inline and level is CodeValidationLevel.UNVALIDATED


def test_runnable_claim_policy_preserves_unfenced_source_literals_and_rewrites_prose_claims():
    answer = '''经过测试的完整示例，已经运行成功并且执行成功：
```python
print("ok")
```
message = "已经测试"
status = '执行成功'
print("可以运行")'''
    normalized, level = apply_runnable_claim_policy(answer)
    assert level is CodeValidationLevel.STATIC_VALIDATED
    assert "经过测试的完整示例" not in normalized
    assert "已经运行成功" not in normalized.split("```", 1)[0]
    assert 'message = "已经测试"' in normalized
    assert "status = '执行成功'" in normalized
    assert 'print("可以运行")' in normalized
    assert "STATIC_VALIDATED" in normalized and "未执行" in normalized

    contextual = '代码没报错，后续没问题。\n```python\nprint("ok")\n```'
    normalized, level = apply_runnable_claim_policy(contextual)
    assert level is CodeValidationLevel.STATIC_VALIDATED
    assert "没报错" not in normalized and "没问题" not in normalized

    inline_literal = '代码没报错，`没有问题` 是字面量。\n```python\nprint("ok")\n```'
    normalized, level = apply_runnable_claim_policy(inline_literal)
    assert level is CodeValidationLevel.STATIC_VALIDATED
    assert "`没有问题`" in normalized
    inline_package = TelegramOutputRenderer().package(normalized)
    assert any("<code>没有问题</code>" in chunk for chunk in inline_package.chunks)


def test_runnable_claim_policy_handles_unfenced_code_and_compound_source_literals():
    claims = (
        "我已在本机实际运行这段代码，结果正常。",
        "这份代码可正常执行。",
        "我刚刚执行了这份程序，确认没有报错。",
        "这段代码我亲自运行过，没有问题。",
        "代码已在本机验证无误。",
        "实测可用。",
        "运行过了，一切正常。",
        "这段代码我跑过了，没问题。",
        "在开发机上验证结果正确。",
        "执行结果符合预期。",
        "本地试跑正常。",
        "这段代码能运行。",
        "这段代码肯定能运行。",
        "这段代码运行无报错。",
        "代码运行没有报错。",
        "代码运行没报错。",
        "代码执行未报错。",
        "代码执行没有问题。",
        "代码没报错。",
        "代码没有问题。",
    )
    for claim in claims:
        answer = f'{claim}\nif ready: status = "已经运行成功"'
        normalized, level = apply_runnable_claim_policy(answer)
        assert level is CodeValidationLevel.UNVALIDATED
        assert claim not in normalized
        assert 'if ready: status = "已经运行成功"' in normalized
        assert "UNVALIDATED" in normalized and "未执行" in normalized
        prose_before_source = normalized.split("if ready:", 1)[0]
        for residue in ("没问题", "没有问题", "无报错", "未报错", "没报错", "没有报错", "一切正常", "结果正常", "结果正确", "符合预期"):
            assert residue not in prose_before_source


def test_runnable_claim_policy_does_not_rewrite_negative_execution_disclosure():
    for disclosure in ("这段代码尚未运行。", "这段代码并未经过测试。", "测试未通过。"):
        answer = f'{disclosure}\nprint("ok")'
        normalized, level = apply_runnable_claim_policy(answer)
        assert normalized == answer
        assert level is CodeValidationLevel.UNVALIDATED

    for mixed in (
        "并未经过测试，但可以运行", "尚未运行，不过测试通过", "测试未通过，但可正常运行",
        "并没有实际执行；开发机验证通过", "尚未运行且测试通过", "从未运行而且可以正常执行",
    ):
        normalized, level = apply_runnable_claim_policy(f'{mixed}\nprint("ok")')
        assert level is CodeValidationLevel.UNVALIDATED
        assert mixed not in normalized
        assert "未执行" in normalized


def test_runnable_claim_policy_preserves_complete_unfenced_source_lines():
    source_lines = (
        "已经测试 = True",
        'if ready: 已经测试 = True',
        'status = "执行成功"  # 已经测试的字面源码',
        'status = 1; message = "已经测试"',
        '# 这段代码已经测试通过',
        '# 第二行注释',
        'print("ok")',
    )
    answer = "我已经执行成功。\n" + "\n".join(source_lines)
    normalized, level = apply_runnable_claim_policy(answer)
    assert level is CodeValidationLevel.UNVALIDATED
    for source in source_lines:
        assert source in normalized


def test_chunk_boundaries_and_empty_text():
    for size in (TELEGRAM_SAFE_CHUNK_SIZE - 1, TELEGRAM_SAFE_CHUNK_SIZE, TELEGRAM_SAFE_CHUNK_SIZE + 1, TELEGRAM_SAFE_CHUNK_SIZE * 3 + 9):
        text = "中" * size
        chunks = chunk_text(text)
        assert "".join(chunks) == text
        assert all(len(chunk) <= TELEGRAM_SAFE_CHUNK_SIZE for chunk in chunks)
    assert chunk_text("")[0]


def test_malformed_structured_candidates_terminate_and_preserve_content():
    renderer = TelegramOutputRenderer()
    candidates = (
        "{not valid json",
        "[1, 2,",
        "{'pattern': '**keep**'}",
        "{item for item in values}",
        '{"partial":\n',
    )
    for candidate in candidates:
        package = renderer.package(candidate)
        assert package.canonical_text == candidate.strip()
        assert "".join(package.visible_chunks) == package.canonical_text
        assert not renderer.has_visible_markdown_artifacts(package.canonical_text, package.protected_ranges)

    multiline_candidates = (
        '{\n  "pattern": "**keep**"\n',
        '[\n  "__keep__"\n',
        "{\n  'pattern': '**keep**'\n}\n",
        "{\n  '**keep**',\n  '__keep__'\n}\n",
        '{"outer": [\n  {"pattern": "**keep**"}\n',
        '{\n  "a": [1, 2}\n  "b": "**keep**"\n}',
        '[\n  {"a": 1]\n  "**keep**"\n]',
        "{bad: '**keep**'}\n[also_bad: '__keep__']",
        "{\n" + "  'row': '**keep**',\n" * 2000,
    )
    for candidate in multiline_candidates:
        package = renderer.package(candidate)
        assert package.canonical_text == candidate.strip()
        assert "".join(package.visible_chunks) == package.canonical_text


def test_output_budget_is_centralized_and_intent_sensitive():
    assert output_budget("你好，简单介绍一下你能做什么") == CHAT_DEFAULT_MAX_OUTPUT_TOKENS
    assert output_budget("请详细解释 Python 装饰器") == CHAT_LONG_MAX_OUTPUT_TOKENS


def test_omlx_response_parser_tolerates_null_output_and_content_items():
    assert extract_text({"output": None}) == ""
    assert extract_text({"output": [{"content": None}, None]}) == ""
    assert extract_text({"output": [{"content": [None, {"type": "output_text", "text": "ok"}]}]}) == "ok"


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


class RuntimeBrokenDecoratorProvider:
    def generate(self, prompt, max_output_tokens=1024):
        return ModelReply('''完整示例：
```python
def require_admin(func):
    def wrapper(*args, **kwargs):
        current_user = kwargs.get("current_user", "guest")
        if current_user != "admin":
            raise PermissionError
        return func(*args, **kwargs)
    return wrapper

@require_admin
def delete_database():
    return "deleted"

delete_database(current_user="admin")
```

```python
def identity(value):
    return value
```''', "completed", None, 100, max_output_tokens)


def test_bug_code_002_runtime_broken_decorator_is_replaced_in_production_chat(public_repo, identities):
    _, user = identities
    session = public_repo.create_session(user)
    result = ChatService(public_repo, RuntimeBrokenDecoratorProvider()).reply(user, session, "请给出两个完整装饰器示例")
    assert 'kwargs.get("current_user"' not in result.text
    assert result.text.count("import functools") == 2
    assert "未在本次对话中执行" in result.text
