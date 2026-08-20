from dataclasses import dataclass


def approximate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class ContextPolicy:
    recent_message_count: int = 12
    recent_token_budget: int = 3000
    summary_token_budget: int = 800


class NoopEmbeddingProvider:
    """Test-only deterministic provider; not production semantic search."""
    status = "PROVIDER_PENDING"

    def embed(self, text: str):
        return [float(len(text) % 17), float(sum(map(ord, text)) % 101)]


class ContextAssembler:
    def __init__(self, policy=ContextPolicy()):
        self.policy = policy

    def assemble(self, recent_messages, summary=None, memories=()):
        selected, used = [], 0
        for row in reversed(list(recent_messages)[-self.policy.recent_message_count:]):
            tokens = approximate_tokens(row["content"])
            if used + tokens > self.policy.recent_token_budget:
                break
            selected.append({"role": row["role"], "content": row["content"]})
            used += tokens
        selected.reverse()
        prefix = []
        if summary:
            prefix.append({"role": "system", "content": "对话摘要：" + summary["content"][: self.policy.summary_token_budget * 4]})
        for memory in memories[:5]:
            prefix.append({"role": "system", "content": f"相关记忆（{memory['subject']}）：{memory['content']}"})
        return prefix + selected


class SummaryService:
    """Summary generation is provider-pluggable; local tests never call a model."""
    def should_summarize(self, messages, threshold=18):
        return len(messages) >= threshold
