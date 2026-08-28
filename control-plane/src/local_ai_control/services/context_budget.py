from __future__ import annotations

from dataclasses import dataclass


DEFAULT_QUALIFIED_TOTAL_CONTEXT_TOKENS = 16_384
DEFAULT_SYSTEM_TOOL_RESERVE_TOKENS = 2_048
MIN_INPUT_BUDGET_TOKENS = 512


class ContextBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class ContextBudget:
    qualified_total_context_tokens: int
    output_reserve_tokens: int
    system_tool_reserve_tokens: int
    modality_reserve_tokens: int
    input_budget_tokens: int

    def __post_init__(self) -> None:
        values = (
            self.qualified_total_context_tokens,
            self.output_reserve_tokens,
            self.system_tool_reserve_tokens,
            self.modality_reserve_tokens,
            self.input_budget_tokens,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in values
        ):
            raise ContextBudgetError("context budget values must be non-negative integers")

        accounted = (
            self.output_reserve_tokens
            + self.system_tool_reserve_tokens
            + self.modality_reserve_tokens
            + self.input_budget_tokens
        )
        if accounted != self.qualified_total_context_tokens:
            raise ContextBudgetError("context budget accounting invariant failed")


class ContextBudgetManager:
    """Host-side budget policy under the currently qualified model envelope.

    Phase 1 deliberately caps the effective total context at 16K even if a
    provider advertises a larger native or runtime limit. Promotion above 16K
    requires a later qualification and policy change.
    """

    def __init__(
        self,
        *,
        qualified_total_context_tokens: int = (
            DEFAULT_QUALIFIED_TOTAL_CONTEXT_TOKENS
        ),
        system_tool_reserve_tokens: int = (
            DEFAULT_SYSTEM_TOOL_RESERVE_TOKENS
        ),
        minimum_input_budget_tokens: int = MIN_INPUT_BUDGET_TOKENS,
    ) -> None:
        for value in (
            qualified_total_context_tokens,
            system_tool_reserve_tokens,
            minimum_input_budget_tokens,
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise ContextBudgetError("invalid context budget policy")

        self.qualified_total_context_tokens = qualified_total_context_tokens
        self.system_tool_reserve_tokens = system_tool_reserve_tokens
        self.minimum_input_budget_tokens = minimum_input_budget_tokens

    @staticmethod
    def _provider_limit(provider) -> int | None:
        value = getattr(provider, "max_context_tokens", None)

        if value is None:
            return None

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1_024
        ):
            raise ContextBudgetError("invalid provider max_context_tokens")

        return value

    def for_provider(
        self,
        provider,
        *,
        output_reserve_tokens: int,
        modality_reserve_tokens: int = 0,
    ) -> ContextBudget:
        if (
            not isinstance(output_reserve_tokens, int)
            or isinstance(output_reserve_tokens, bool)
            or output_reserve_tokens <= 0
        ):
            raise ContextBudgetError("invalid output reserve")

        if (
            not isinstance(modality_reserve_tokens, int)
            or isinstance(modality_reserve_tokens, bool)
            or modality_reserve_tokens < 0
        ):
            raise ContextBudgetError("invalid modality reserve")

        provider_limit = self._provider_limit(provider)

        effective_total = self.qualified_total_context_tokens

        if provider_limit is not None:
            effective_total = min(effective_total, provider_limit)

        input_budget = (
            effective_total
            - output_reserve_tokens
            - self.system_tool_reserve_tokens
            - modality_reserve_tokens
        )

        if input_budget < self.minimum_input_budget_tokens:
            raise ContextBudgetError(
                "insufficient input budget inside qualified context envelope"
            )

        return ContextBudget(
            qualified_total_context_tokens=effective_total,
            output_reserve_tokens=output_reserve_tokens,
            system_tool_reserve_tokens=self.system_tool_reserve_tokens,
            modality_reserve_tokens=modality_reserve_tokens,
            input_budget_tokens=input_budget,
        )
