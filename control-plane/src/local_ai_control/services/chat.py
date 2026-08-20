from dataclasses import dataclass

from local_ai_control.services.memory import ContextAssembler
from local_ai_control.services.security import SECRET_BLOCK_MESSAGE, SecretFirewall


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
            + "\n".join(f"{item['role']}: {item['content']}" for item in context)
        )
        reply = self.provider.generate(prompt, max_output_tokens=output_budget(message))
        if not reply.text:
            return ChatResult("模型暂时没有返回可显示的内容，请稍后重试。", False, reply.incomplete_reason or reply.status, reply.output_tokens, reply.requested_max_output_tokens)
        if not reply.complete:
            return ChatResult("回答尚未完整，为避免发送半句话，请重新提问或缩小问题范围。", False, reply.incomplete_reason or reply.status, reply.output_tokens, reply.requested_max_output_tokens)
        self.repository.add_message(identity, session_id, "assistant", reply.text)
        return ChatResult(reply.text, True, reply.status, reply.output_tokens, reply.requested_max_output_tokens)
