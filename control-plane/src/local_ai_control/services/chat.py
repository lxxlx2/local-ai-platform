from dataclasses import dataclass
import re

from local_ai_control.services.memory import ContextAssembler
from local_ai_control.services.security import SECRET_BLOCK_MESSAGE, SecretFirewall
from local_ai_control.services.code_quality import CodeValidationLevel, check_python_block, python_blocks
from local_ai_control.services.output import TelegramOutputRenderer


CHAT_DEFAULT_MAX_OUTPUT_TOKENS = 1024
CHAT_LONG_MAX_OUTPUT_TOKENS = 4096
CONTROL_INTENT_MAX_OUTPUT_TOKENS = 300


@dataclass(frozen=True)
class ChatResult:
    text: str
    complete: bool
    finish_reason: str | None
    output_tokens: int | None
    requested_max_output_tokens: int


def output_budget(message: str) -> int:
    long_words = ("详细", "全面", "完整", "深入", "逐步", "完整流程")
    return CHAT_LONG_MAX_OUTPUT_TOKENS if any(word in message for word in long_words) else CHAT_DEFAULT_MAX_OUTPUT_TOKENS


def supplied_code_lines(message: str):
    """Keep a user-supplied standalone assignment visible in a code explanation."""
    return [line.strip() for line in message.splitlines() if re.match(r"^[A-Za-z_][\w.\[\]]*\s*=\s*.+", line.strip())]


DECORATOR_EXAMPLES = """\n\n补充：以下两个示例已通过静态检查，但未在本次对话中执行。\n\n```python
import functools

def log_calls(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        print(f"调用 {func.__name__}")
        return func(*args, **kwargs)
    return wrapped

@log_calls
def add(left, right):
    return left + right

print(add(2, 3))
```

```python
import functools

def require_level(required_level):
    def decorate(func):
        @functools.wraps(func)
        def wrapped(user_level, *args, **kwargs):
            if user_level < required_level:
                raise PermissionError("权限不足")
            return func(user_level, *args, **kwargs)
        return wrapped
    return decorate

@require_level(3)
def publish(user_level, title):
    return f"已发布：{title}"

print(publish(3, "示例"))
```"""


def needs_standalone_decorator_examples(message: str, answer: str) -> bool:
    if "装饰器" not in message or not any(word in message for word in ("完整", "两个", "示例")):
        return False
    blocks = python_blocks(answer)
    return len(blocks) < 2 or not all(check_python_block(block).syntax_valid and check_python_block(block).standalone_claim_ok for block in blocks)


def replace_invalid_decorator_examples(answer: str) -> str:
    """Do not leave a runnable-looking invalid example beside the safe fallback."""
    without_python = re.sub(r"```python\s*\n[\s\S]*?```", "", answer, flags=re.I).rstrip()
    return without_python + DECORATOR_EXAMPLES


def apply_runnable_claim_policy(answer: str):
    """Unknown model code is never described as execution-validated."""
    blocks = python_blocks(answer)
    renderer = TelegramOutputRenderer()
    has_unfenced_source = renderer.has_source_context(answer)
    if not blocks and not has_unfenced_source:
        return answer, None
    complete_claim = re.compile(r"完整(?:代码)?示例")
    claim_found = False

    def affirmative_execution_clause(clause):
        action = r"运行|执行|测试|验证|实测|试跑|跑"
        positive_outcome = r"没问题|没有问题|无报错|未报错|没报错|没有报错|一切正常|结果正常|结果正确|符合预期"
        if not re.search(action, clause):
            if re.search(r"如果|若|是否|可能|也许|希望|字面量|字符串|变量名|术语|表示|含义", clause):
                return False
            stripped = clause.strip()
            return bool(re.search(positive_outcome, stripped))
        if re.search(rf"(?:并未|尚未|未曾|没有|没|不曾|无需|不要|无法|不能|不可|未)(?:[^，。！？\n]{{0,8}})(?:{action})", clause):
            return False
        if re.search(rf"(?:{action})(?:[^，。！？\n]{{0,8}})(?:未|没有|没)(?:通过|成功|正常|完成)", clause):
            return False
        return bool(re.search(
            rf"实测|试跑|亲自|实际|本机|本地|开发机|刚刚|"
            rf"(?:已|已经|经过|经)(?:[^，。！？\n]{{0,10}})(?:{action})|"
            rf"(?:{action})(?:过|了|成功|通过|正常)|"
            rf"(?:{action})(?:[^，。！？\n]{{0,12}})(?:成功|正常|通过|无误|可用|{positive_outcome}|正确)|"
            rf"(?:可|可以|能够|能|肯定能)(?:[^，。！？\n]{{0,6}})(?:运行|执行|试跑)"
        , clause))

    def contains_execution_claim(prose):
        return any(affirmative_execution_clause(clause) for clause in re.split(r"[。！？\n，,；;]|但(?:是)?|不过|然而|而且|并且|同时|且", prose))

    def replace_execution_claims(prose, replacement):
        parts = re.split(r"([。！？\n，,；;]|但(?:是)?|不过|然而|而且|并且|同时|且)", prose)
        for index in range(0, len(parts), 2):
            if affirmative_execution_clause(parts[index]):
                parts[index] = replacement
        return "".join(parts)

    def find_claims(prose):
        nonlocal claim_found
        claim_found = bool(contains_execution_claim(prose) or complete_claim.search(prose))
        return prose

    renderer.transform_unprotected_prose(answer, find_claims)
    if not claim_found:
        return answer, CodeValidationLevel.UNVALIDATED
    static_ok = bool(blocks) and all(check_python_block(block).syntax_valid and check_python_block(block).standalone_claim_ok for block in blocks)
    level = CodeValidationLevel.STATIC_VALIDATED if static_ok else CodeValidationLevel.UNVALIDATED
    replacement = "已通过静态检查（未执行）" if static_ok else "未通过执行验证（未执行）"

    def normalize_claims(prose):
        return replace_execution_claims(prose, replacement)

    prose = renderer.transform_unprotected_prose(answer, normalize_claims)
    detail = "已通过静态检查，未执行" if level is CodeValidationLevel.STATIC_VALIDATED else "未通过完整静态验收，未执行"
    notice = f"\n\n代码验证级别：{level.value}（{detail}）。"
    return prose.rstrip() + notice, level


class ChatService:
    """Chat has no filesystem, shell, Git, or control capabilities."""
    def __init__(self, repository, provider, firewall=None, assembler=None):
        self.repository = repository
        self.provider = provider
        self.firewall = firewall or SecretFirewall()
        self.assembler = assembler or ContextAssembler()

    def reply(self, identity, session_id, message):
        decision = self.firewall.inspect(message)
        if decision.action == "BLOCK":
            return ChatResult(SECRET_BLOCK_MESSAGE, True, "blocked", None, 0)
        self.repository.add_message(identity, session_id, "user", message)
        messages = self.repository.recent_messages(identity, session_id, self.assembler.policy.recent_message_count)
        summary = self.repository.get_summary(identity, session_id)
        memories = self.repository.list_memories(identity, limit=5)
        context = self.assembler.assemble(messages, summary, memories)
        prompt = (
            "你是可靠的中文 AI 助手。只回答用户问题；你没有系统、文件、Git 或私有数据访问能力。"
            "普通回答应直接、自然、适合手机阅读，默认简洁；只有用户明确要求详细、全面、深入或逐步说明时才展开。"
            "普通文本避免 Markdown 标题和加粗标记。代码、JSON、命令或用户提供的字面量必须原样保留。\n\n"
            "如果你说某个 Python 示例可以独立运行，请在该代码块内包含所有必要 import，并让类型说明与示例参数一致。"
            + "\n".join(f"{item['role']}: {item['content']}" for item in context)
        )
        reply = self.provider.generate(prompt, max_output_tokens=output_budget(message))
        if not reply.text:
            return ChatResult("模型暂时没有返回可显示的内容，请稍后重试。", False, reply.incomplete_reason or reply.status, reply.output_tokens, reply.requested_max_output_tokens)
        if not reply.complete:
            return ChatResult("回答尚未完整，为避免发送半句话，请重新提问或缩小问题范围。", False, reply.incomplete_reason or reply.status, reply.output_tokens, reply.requested_max_output_tokens)
        answer = reply.text
        if needs_standalone_decorator_examples(message, answer):
            answer = replace_invalid_decorator_examples(answer)
        answer, _validation_level = apply_runnable_claim_policy(answer)
        for line in supplied_code_lines(message):
            if line not in answer:
                answer += "\n\n代码：\n" + line
        self.repository.add_message(identity, session_id, "assistant", answer)
        return ChatResult(answer, True, reply.status, reply.output_tokens, reply.requested_max_output_tokens)
