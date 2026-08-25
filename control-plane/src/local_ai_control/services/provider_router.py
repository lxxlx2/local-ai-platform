from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Protocol


class Capability(StrEnum):
    REASONING = "REASONING"
    PLANNING = "PLANNING"
    CODE = "CODE"
    REVIEW = "REVIEW"
    MULTIMODAL = "MULTIMODAL"
    IMAGE = "IMAGE"
    EMBEDDING = "EMBEDDING"
    RERANK = "RERANK"
    STT = "STT"
    TTS = "TTS"
    VIDEO = "VIDEO"


class InvocationPurpose(StrEnum):
    ROUTINE = "ROUTINE"
    PLANNING = "PLANNING"
    REVIEW = "REVIEW"
    ACCEPTANCE = "ACCEPTANCE"
    ESCALATION = "ESCALATION"


class PrivacyMode(StrEnum):
    PUBLIC = "PUBLIC"
    RESTRICTED = "RESTRICTED"
    PRIVATE = "PRIVATE"


class ProviderKind(StrEnum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


class QuotaClass(StrEnum):
    NONE = "NONE"
    GEMINI = "GEMINI"
    CODEX = "CODEX"


ALL_PURPOSES = frozenset(InvocationPurpose)
LOCAL_DEFAULT_PURPOSES = ALL_PURPOSES
GEMINI_PURPOSES = frozenset({
    InvocationPurpose.PLANNING,
    InvocationPurpose.REVIEW,
    InvocationPurpose.ACCEPTANCE,
    InvocationPurpose.ESCALATION,
})
CODEX_PREMIUM_PURPOSES = frozenset({
    InvocationPurpose.PLANNING,
    InvocationPurpose.ACCEPTANCE,
    InvocationPurpose.ESCALATION,
})


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    display_name: str
    kind: ProviderKind
    capabilities: frozenset[Capability]
    allowed_purposes: frozenset[InvocationPurpose] = ALL_PURPOSES
    quota_class: QuotaClass = QuotaClass.NONE
    enabled: bool = True
    allows_restricted_egress: bool = False
    requires_egress_gate: bool = False
    priority: int = 100


@dataclass(frozen=True)
class ProviderRequest:
    capability: Capability
    privacy: PrivacyMode
    purpose: InvocationPurpose = InvocationPurpose.ROUTINE
    explicit_provider: str | None = None
    sanitized_for_egress: bool = False
    premium_codex_allowed: bool = False


@dataclass(frozen=True)
class ProviderSelection:
    provider: ProviderProfile
    reason: str
    consumes_codex_quota: bool
    quota_class: QuotaClass


class ProviderHealth(Protocol):
    def __call__(self, provider: ProviderProfile) -> bool: ...


class ProviderRouter:
    """Local-first cross-provider router.

    The router chooses a model/provider. It never grants filesystem, shell, Git,
    network, deployment, download or service-control authority.

    Policy invariants:
    - ROUTINE work never routes to the OpenAI Codex model.
    - Codex-model quota is opt-in and limited to planning/acceptance/escalation.
    - PRIVATE work never routes to cloud providers.
    - RESTRICTED cloud work requires an explicit completed egress gate.
    - Gemini is preferred for independent review when privacy permits.
    - local providers remain the default workers for routine production.
    """

    def __init__(self, providers: Iterable[ProviderProfile], *, health: ProviderHealth | None = None):
        values = tuple(providers)
        ids = [item.provider_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate provider_id")
        self.providers = {item.provider_id: item for item in values}
        self.health = health or (lambda _provider: True)

    @staticmethod
    def _privacy_allows(provider: ProviderProfile, request: ProviderRequest) -> bool:
        if provider.kind is ProviderKind.LOCAL:
            return True
        if request.privacy is PrivacyMode.PRIVATE:
            return False
        if request.privacy is PrivacyMode.RESTRICTED:
            return bool(
                provider.allows_restricted_egress
                and provider.requires_egress_gate
                and request.sanitized_for_egress
            )
        return True

    @staticmethod
    def _quota_allows(provider: ProviderProfile, request: ProviderRequest) -> bool:
        if provider.quota_class is not QuotaClass.CODEX:
            return True
        if request.purpose is InvocationPurpose.ROUTINE:
            return False
        return bool(
            request.premium_codex_allowed
            and request.purpose in CODEX_PREMIUM_PURPOSES
        )

    def _eligible(self, provider: ProviderProfile, request: ProviderRequest) -> bool:
        return bool(
            provider.enabled
            and request.capability in provider.capabilities
            and request.purpose in provider.allowed_purposes
            and self._privacy_allows(provider, request)
            and self._quota_allows(provider, request)
            and self.health(provider)
        )

    @staticmethod
    def _sort_key(provider: ProviderProfile, request: ProviderRequest) -> tuple[int, int, str]:
        # Independent review prefers Gemini when privacy permits.
        if request.purpose is InvocationPurpose.REVIEW and provider.provider_id == "gemini":
            tier = 0
        # Premium Codex is used only when the caller explicitly opens the budget
        # gate for planning/acceptance/escalation.
        elif (
            request.premium_codex_allowed
            and request.purpose in CODEX_PREMIUM_PURPOSES
            and provider.provider_id == "openai-codex"
        ):
            tier = 0
        elif provider.kind is ProviderKind.LOCAL:
            tier = 1
        else:
            tier = 2
        return tier, provider.priority, provider.provider_id

    def route(self, request: ProviderRequest) -> ProviderSelection:
        if request.explicit_provider:
            provider = self.providers.get(request.explicit_provider)
            if provider is None:
                raise LookupError("requested provider is not registered")
            if not self._eligible(provider, request):
                raise PermissionError("requested provider is not eligible for this request")
            return ProviderSelection(
                provider=provider,
                reason="explicit_provider",
                consumes_codex_quota=provider.quota_class is QuotaClass.CODEX,
                quota_class=provider.quota_class,
            )

        candidates = [item for item in self.providers.values() if self._eligible(item, request)]
        if not candidates:
            raise LookupError("no healthy provider satisfies capability/privacy/purpose policy")
        candidates.sort(key=lambda item: self._sort_key(item, request))
        provider = candidates[0]
        return ProviderSelection(
            provider=provider,
            reason="local_first_policy",
            consumes_codex_quota=provider.quota_class is QuotaClass.CODEX,
            quota_class=provider.quota_class,
        )


LOCAL_QWEN_PROVIDER = ProviderProfile(
    provider_id="local-qwen",
    display_name="Local Qwen",
    kind=ProviderKind.LOCAL,
    capabilities=frozenset({
        Capability.REASONING,
        Capability.PLANNING,
        Capability.CODE,
        Capability.REVIEW,
        Capability.MULTIMODAL,
    }),
    allowed_purposes=LOCAL_DEFAULT_PURPOSES,
    quota_class=QuotaClass.NONE,
    priority=20,
)

GEMINI_PROVIDER = ProviderProfile(
    provider_id="gemini",
    display_name="Gemini Cloud",
    kind=ProviderKind.CLOUD,
    capabilities=frozenset({
        Capability.REASONING,
        Capability.PLANNING,
        Capability.REVIEW,
        Capability.MULTIMODAL,
    }),
    allowed_purposes=GEMINI_PURPOSES,
    quota_class=QuotaClass.GEMINI,
    allows_restricted_egress=True,
    requires_egress_gate=True,
    priority=20,
)

OPENAI_CODEX_PROVIDER = ProviderProfile(
    provider_id="openai-codex",
    display_name="OpenAI Codex Premium",
    kind=ProviderKind.CLOUD,
    capabilities=frozenset({
        Capability.REASONING,
        Capability.PLANNING,
        Capability.REVIEW,
    }),
    allowed_purposes=CODEX_PREMIUM_PURPOSES,
    quota_class=QuotaClass.CODEX,
    allows_restricted_egress=True,
    requires_egress_gate=True,
    priority=10,
)


def default_provider_router(*, health: ProviderHealth | None = None) -> ProviderRouter:
    return ProviderRouter(
        (LOCAL_QWEN_PROVIDER, GEMINI_PROVIDER, OPENAI_CODEX_PROVIDER),
        health=health,
    )
