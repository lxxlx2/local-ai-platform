from __future__ import annotations

from dataclasses import dataclass

from local_ai_control.services.context_budget import ContextBudgetError


def approximate_tokens(text: str) -> int:
    """Lightweight conservative host-side estimate.

    The provider/runtime tokenizer remains authoritative.
    CJK characters are counted individually while other
    characters use a rough four-characters-per-token estimate.
    """
    if not isinstance(text, str):
        raise TypeError("context text must be a string")

    if not text:
        return 0

    cjk = sum(
        1
        for char in text
        if (
            "\u3400" <= char <= "\u4dbf"
            or "\u4e00" <= char <= "\u9fff"
            or "\uf900" <= char <= "\ufaff"
        )
    )

    other = len(text) - cjk

    return max(
        1,
        cjk + (other + 3) // 4,
    )


def _truncate_to_token_budget(
    text: str,
    token_budget: int,
) -> str:
    if token_budget <= 0:
        return ""

    if approximate_tokens(text) <= token_budget:
        return text

    low = 0
    high = len(text)

    while low < high:
        middle = (low + high + 1) // 2

        if approximate_tokens(text[:middle]) <= token_budget:
            low = middle
        else:
            high = middle - 1

    return text[:low]


@dataclass(frozen=True)
class ContextPolicy:
    # Backward-compatible direct ContextAssembler contract.
    recent_message_count: int = 12
    recent_token_budget: int = 3000

    # ChatService may fetch a larger bounded history and then
    # explicitly apply the ContextBudgetManager token budget.
    history_fetch_count: int = 32

    summary_token_budget: int = 800
    memory_token_budget: int = 1600
    memory_count: int = 5


class NoopEmbeddingProvider:
    """Test-only deterministic provider; not production semantic search."""

    status = "PROVIDER_PENDING"

    def embed(self, text: str):
        return [
            float(len(text) % 17),
            float(sum(map(ord, text)) % 101),
        ]


class ContextAssembler:
    def __init__(
        self,
        policy=ContextPolicy(),
    ):
        self.policy = policy

    def assemble(
        self,
        recent_messages,
        summary=None,
        memories=(),
        *,
        token_budget: int | None = None,
    ):
        dynamic_budget = token_budget is not None

        # Preserve the historic public/default behavior when a caller
        # does not explicitly opt into ContextBudgetManager budgeting.
        if token_budget is None:
            token_budget = self.policy.recent_token_budget

        if (
            not isinstance(token_budget, int)
            or isinstance(token_budget, bool)
            or token_budget <= 0
        ):
            raise ContextBudgetError(
                "invalid context input budget"
            )

        row_limit = (
            self.policy.history_fetch_count
            if dynamic_budget
            else self.policy.recent_message_count
        )

        rows = list(recent_messages)[-row_limit:]

        latest_tokens = 0

        if rows:
            latest_tokens = approximate_tokens(
                rows[-1]["content"]
            )

            if latest_tokens > token_budget:
                raise ContextBudgetError(
                    "latest message exceeds available "
                    "context input budget"
                )

        # Optional prefix material and recent conversation now
        # participate in one total budget.
        prefix_room = max(
            0,
            token_budget - latest_tokens,
        )

        prefix = []
        prefix_used = 0

        if summary and prefix_room > 0:
            raw_summary = (
                "对话摘要："
                + summary["content"]
            )

            summary_limit = min(
                self.policy.summary_token_budget,
                prefix_room,
            )

            bounded_summary = (
                _truncate_to_token_budget(
                    raw_summary,
                    summary_limit,
                )
            )

            if bounded_summary:
                tokens = approximate_tokens(
                    bounded_summary
                )

                prefix.append(
                    {
                        "role": "system",
                        "content": bounded_summary,
                    }
                )

                prefix_used += tokens
                prefix_room -= tokens

        memory_room = min(
            self.policy.memory_token_budget,
            prefix_room,
        )

        for memory in list(memories)[
            : self.policy.memory_count
        ]:
            if (
                memory_room <= 0
                or prefix_room <= 0
            ):
                break

            raw_memory = (
                f"相关记忆（{memory['subject']}）："
                f"{memory['content']}"
            )

            bounded_memory = (
                _truncate_to_token_budget(
                    raw_memory,
                    min(
                        memory_room,
                        prefix_room,
                    ),
                )
            )

            if not bounded_memory:
                break

            tokens = approximate_tokens(
                bounded_memory
            )

            prefix.append(
                {
                    "role": "system",
                    "content": bounded_memory,
                }
            )

            prefix_used += tokens
            prefix_room -= tokens
            memory_room -= tokens

        recent_room = (
            token_budget
            - prefix_used
        )

        selected = []
        recent_used = 0

        for row in reversed(rows):
            tokens = approximate_tokens(
                row["content"]
            )

            if (
                recent_used + tokens
                > recent_room
            ):
                # Older history is optional. Never silently
                # truncate an individual conversational message.
                break

            selected.append(
                {
                    "role": row["role"],
                    "content": row["content"],
                }
            )

            recent_used += tokens

        selected.reverse()

        total_used = (
            prefix_used
            + recent_used
        )

        if total_used > token_budget:
            raise AssertionError(
                "context assembler budget invariant failed"
            )

        return prefix + selected


class SummaryService:
    """Summary generation is provider-pluggable; local tests never call a model."""

    def should_summarize(
        self,
        messages,
        threshold=18,
    ):
        return len(messages) >= threshold
