from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Iterable


class Capability(StrEnum):
    REASONING = "REASONING"
    CODE = "CODE"
    REVIEW = "REVIEW"
    MULTIMODAL = "MULTIMODAL"
    IMAGE = "IMAGE"
    EMBEDDING = "EMBEDDING"
    RERANK = "RERANK"
    STT = "STT"
    TTS = "TTS"


class PrivacyMode(StrEnum):
    PUBLIC = "PUBLIC"
    RESTRICTED = "RESTRICTED"
    PRIVATE = "PRIVATE"


class ProviderKind(StrEnum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    display_name: str
    kind: ProviderKind
    capabilities: frozenset[Capability]
    enabled: bool = True
    allows_restricted_egress: bool = False
    requires_egress_gate: bool = False
    priority: int = 100


@dataclass(frozen=True)
class ProviderRequest:
    capability: Capability
    privacy: PrivacyMode
    explicit_provider: str | None = None
    sanitized_for_egress: bool = False


@dataclass(frozen=True)
class ProviderSelection:
    provider: ProviderProfile
    reason: str


class ProviderHealth(Protocol):
    def __call__(self, provider: ProviderProfile) -> bool: ...


class ProviderRouter:
    """Routes between local and cloud providers without granting execution authority.

    This layer chooses a provider only. Repository/shell/Git permissions remain owned
    by Supervisor/Codex policy. PRIVATE requests can never leave the Mac. RESTRICTED
    requests may use a cloud provider only after the caller has explicitly completed
    the egress sanitization gate.
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

    def _eligible(self, provider: ProviderProfile, request: ProviderRequest) -> bool:
        return bool(
            provider.enabled
            and request.capability in provider.capabilities
            and self._privacy_allows(provider, request)
            and self.health(provider)
        )

    def route(self, request: ProviderRequest) -> ProviderSelection:
        if request.explicit_provider:
            provider = self.providers.get(request.explicit_provider)
            if provider is None:
                raise LookupError("requested provider is not registered")
            if not self._eligible(provider, request):
                raise PermissionError("requested provider is not eligible for this request")
            return ProviderSelection(provider, "explicit_provider")

        candidates = [item for item in self.providers.values() if self._eligible(item, request)]
        if not candidates:
            raise LookupError("no healthy provider satisfies capability/privacy policy")
        candidates.sort(key=lambda item: (item.priority, item.provider_id))
        return ProviderSelection(candidates[0], "policy_priority")


LOCAL_QWEN_PROVIDER = ProviderProfile(
    provider_id="local-qwen",
    display_name="Local Qwen",
    kind=ProviderKind.LOCAL,
    capabilities=frozenset({
        Capability.REASONING,
        Capability.CODE,
        Capability.REVIEW,
        Capability.MULTIMODAL,
    }),
    priority=20,
)

GEMINI_PROVIDER = ProviderProfile(
    provider_id="gemini",
    display_name="Gemini Cloud",
    kind=ProviderKind.CLOUD,
    capabilities=frozenset({
        Capability.REASONING,
        Capability.REVIEW,
        Capability.MULTIMODAL,
    }),
    allows_restricted_egress=True,
    requires_egress_gate=True,
    priority=30,
)


def default_provider_router(*, health: ProviderHealth | None = None) -> ProviderRouter:
    return ProviderRouter((LOCAL_QWEN_PROVIDER, GEMINI_PROVIDER), health=health)
