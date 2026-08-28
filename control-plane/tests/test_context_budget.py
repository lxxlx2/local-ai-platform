import pytest

from local_ai_control.domain.identity import identity_from_telegram
from local_ai_control.services.chat import ChatService
from local_ai_control.services.context_budget import (
    ContextBudgetError,
    ContextBudgetManager,
)
from local_ai_control.services.memory import (
    ContextAssembler,
    ContextPolicy,
    approximate_tokens,
)
from local_ai_control.services.omlx import ModelReply
from local_ai_control.services.storage import ScopedSQLiteRepository


class LargeContextProvider:
    max_context_tokens = 32_768


class SmallContextProvider:
    max_context_tokens = 8_192


def test_standard_manager_caps_provider_at_current_qualified_16k():
    budget = ContextBudgetManager().for_provider(
        LargeContextProvider(),
        output_reserve_tokens=1_024,
    )

    assert budget.qualified_total_context_tokens == 16_384
    assert budget.system_tool_reserve_tokens == 2_048
    assert budget.output_reserve_tokens == 1_024
    assert budget.input_budget_tokens == 13_312


def test_long_answer_reserves_output_inside_same_16k_envelope():
    budget = ContextBudgetManager().for_provider(
        LargeContextProvider(),
        output_reserve_tokens=4_096,
    )

    assert budget.qualified_total_context_tokens == 16_384
    assert budget.input_budget_tokens == 10_240


def test_provider_with_smaller_limit_reduces_effective_budget():
    budget = ContextBudgetManager().for_provider(
        SmallContextProvider(),
        output_reserve_tokens=1_024,
    )

    assert budget.qualified_total_context_tokens == 8_192
    assert budget.input_budget_tokens == 5_120


def test_budget_fails_closed_when_output_consumes_safe_input_room():
    with pytest.raises(ContextBudgetError):
        ContextBudgetManager().for_provider(
            SmallContextProvider(),
            output_reserve_tokens=6_000,
        )


def test_token_estimator_is_more_conservative_for_cjk():
    assert approximate_tokens("中" * 1_000) == 1_000
    assert approximate_tokens("a" * 4_000) == 1_000


def test_assembler_can_use_more_than_legacy_3k_recent_budget():
    assembler = ContextAssembler()

    messages = [
        {
            "role": (
                "user"
                if index % 2 == 0
                else "assistant"
            ),
            "content": chr(65 + index) * 6_000,
        }
        for index in range(4)
    ]

    context = assembler.assemble(
        messages,
        token_budget=8_000,
    )

    used = sum(
        approximate_tokens(item["content"])
        for item in context
    )

    assert used > 3_000
    assert used <= 8_000
    assert context[-1]["content"] == messages[-1]["content"]


def test_summary_memories_and_history_share_total_budget():
    assembler = ContextAssembler()

    messages = [
        {
            "role": "user",
            "content": "最新问题" * 200,
        }
    ]

    summary = {
        "content": "摘要" * 5_000,
    }

    memories = [
        {
            "subject": f"memory-{index}",
            "content": "记忆内容" * 3_000,
        }
        for index in range(5)
    ]

    context = assembler.assemble(
        messages,
        summary,
        memories,
        token_budget=4_000,
    )

    used = sum(
        approximate_tokens(item["content"])
        for item in context
    )

    assert used <= 4_000

    assert (
        context[-1]["content"]
        == messages[-1]["content"]
    )

    system_used = sum(
        approximate_tokens(item["content"])
        for item in context
        if item["role"] == "system"
    )

    assert system_used <= (
        assembler.policy.summary_token_budget
        + assembler.policy.memory_token_budget
    )


def test_latest_message_is_never_silently_truncated():
    assembler = ContextAssembler()

    with pytest.raises(ContextBudgetError):
        assembler.assemble(
            [
                {
                    "role": "user",
                    "content": "中" * 3_000,
                }
            ],
            token_budget=2_000,
        )


class CaptureProvider:
    max_context_tokens = 32_768

    def __init__(self):
        self.calls = []

    def generate(
        self,
        prompt,
        max_output_tokens=1_024,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "max_output_tokens": max_output_tokens,
            }
        )

        return ModelReply(
            "ok",
            "completed",
            None,
            1,
            max_output_tokens,
        )


class CaptureAssembler:
    def __init__(self):
        self.policy = ContextPolicy()
        self.token_budgets = []

    def assemble(
        self,
        recent_messages,
        summary=None,
        memories=(),
        *,
        token_budget=None,
    ):
        self.token_budgets.append(token_budget)

        return [
            {
                "role": "user",
                "content": recent_messages[-1]["content"],
            }
        ]


def test_chat_uses_dynamic_input_budget(
    tmp_path,
):
    repository = ScopedSQLiteRepository(
        tmp_path / "context-chat.db",
        "public",
    )
    repository.migrate()

    try:
        identity = identity_from_telegram(
            100,
            "1",
        )

        session = repository.create_session(
            identity,
        )

        provider = CaptureProvider()
        assembler = CaptureAssembler()

        service = ChatService(
            repository,
            provider,
            assembler=assembler,
        )

        first = service.reply(
            identity,
            session,
            "简单回答",
        )

        second = service.reply(
            identity,
            session,
            "请详细解释这个问题",
        )

        assert first.complete
        assert second.complete

        assert assembler.token_budgets == [
            13_312,
            10_240,
        ]

        assert [
            item["max_output_tokens"]
            for item in provider.calls
        ] == [
            1_024,
            4_096,
        ]

    finally:
        repository.close()


def test_chat_fails_closed_before_provider_on_oversized_turn(
    tmp_path,
):
    repository = ScopedSQLiteRepository(
        tmp_path / "context-limit.db",
        "public",
    )
    repository.migrate()

    try:
        identity = identity_from_telegram(
            101,
            "1",
        )

        session = repository.create_session(
            identity,
        )

        provider = CaptureProvider()

        result = ChatService(
            repository,
            provider,
        ).reply(
            identity,
            session,
            "中" * 14_000,
        )

        assert not result.complete
        assert result.finish_reason == "context_limit"
        assert provider.calls == []

    finally:
        repository.close()


def test_direct_assembler_preserves_legacy_default_contract():
    assembler = ContextAssembler()

    messages = [
        {
            "role": "user",
            "content": "x" * 200 + str(index),
        }
        for index in range(20)
    ]

    context = assembler.assemble(messages)

    assert len(context) <= 12

    assert sum(
        approximate_tokens(item["content"])
        for item in context
    ) <= 3_000


def test_explicit_dynamic_budget_can_use_larger_history_window():
    assembler = ContextAssembler()

    messages = [
        {
            "role": "user",
            "content": "x" * 800 + str(index),
        }
        for index in range(20)
    ]

    legacy = assembler.assemble(messages)

    dynamic = assembler.assemble(
        messages,
        token_budget=8_000,
    )

    assert len(legacy) <= 12
    assert len(dynamic) > len(legacy)

    assert sum(
        approximate_tokens(item["content"])
        for item in dynamic
    ) <= 8_000
