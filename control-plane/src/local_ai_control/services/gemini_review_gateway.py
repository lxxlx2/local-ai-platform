from __future__ import annotations

from dataclasses import dataclass

from .cloud_egress import CloudEgressDecision, CloudEgressGate
from .gemini_provider import GeminiReviewResult, GeminiReviewerProvider
from .provider_router import PrivacyMode


@dataclass(frozen=True)
class GatedGeminiReviewResult:
    review: GeminiReviewResult
    egress: CloudEgressDecision


class GeminiReviewGateway:
    """The only supported application entry for Gemini review material.

    Callers provide raw local review material plus a privacy classification. The
    gateway performs the egress decision first, then invokes the read-only Gemini
    provider using only the approved/minimized material. Raw material is never
    returned in the result or expected to be persisted in cloud audit metadata.
    """

    def __init__(
        self,
        *,
        provider: GeminiReviewerProvider | None = None,
        egress_gate: CloudEgressGate | None = None,
    ):
        self.provider = provider or GeminiReviewerProvider()
        self.egress_gate = egress_gate or CloudEgressGate()

    def review(self, *, material: str, privacy: PrivacyMode) -> GatedGeminiReviewResult:
        egress = self.egress_gate.require(material, privacy)
        review = self.provider.review(
            material=egress.material,
            privacy=privacy,
            sanitized_for_egress=egress.sanitized_for_egress,
        )
        return GatedGeminiReviewResult(review=review, egress=egress)
