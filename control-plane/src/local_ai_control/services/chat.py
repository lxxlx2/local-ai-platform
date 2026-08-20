from local_ai_control.domain.identity import Role
from local_ai_control.services.memory import ContextAssembler
from local_ai_control.services.security import SECRET_BLOCK_MESSAGE, SecretFirewall


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
            return SECRET_BLOCK_MESSAGE
        self.repository.add_message(identity, session_id, "user", message)
        messages = self.repository.recent_messages(identity, session_id, self.assembler.policy.recent_message_count)
        summary = self.repository.get_summary(identity, session_id)
        memories = self.repository.list_memories(identity, limit=5)
        context = self.assembler.assemble(messages, summary, memories)
        prompt = "你是可靠的中文 AI 助手。只回答用户问题；你没有系统、文件、Git 或私有数据访问能力。\n\n" + "\n".join(f"{item['role']}: {item['content']}" for item in context)
        response = self.provider.generate(prompt)
        text = extract_text(response)
        self.repository.add_message(identity, session_id, "assistant", text)
        return text


def extract_text(response):
    if isinstance(response, dict):
        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        for item in response.get("output", []):
            for content in item.get("content", []) if isinstance(item, dict) else []:
                if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                    return content["text"]
    return "模型暂时没有返回可显示的内容，请稍后重试。"
