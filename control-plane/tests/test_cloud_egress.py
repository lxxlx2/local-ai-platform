import pytest

from local_ai_control.services.cloud_egress import (
    CloudEgressDenied,
    CloudEgressGate,
    EgressAction,
)
from local_ai_control.services.gemini_provider import GeminiReviewResult
from local_ai_control.services.gemini_review_gateway import GeminiReviewGateway
from local_ai_control.services.provider_router import PrivacyMode
from local_ai_control.services.security import SecretFirewall


def test_private_material_never_leaves_mac():
    decision = CloudEgressGate().prepare("private novel material", PrivacyMode.PRIVATE)
    assert decision.action is EgressAction.DENY
    assert decision.material == ""
    assert decision.reason == "private_cloud_egress_denied"


def test_public_secret_is_blocked_instead_of_redacted():
    fake_google_key = "AIza" + ("A" * 35)
    decision = CloudEgressGate().prepare(
        f"API credential: {fake_google_key}",
        PrivacyMode.PUBLIC,
    )
    assert decision.action is EgressAction.DENY
    assert decision.material == ""
    assert decision.reason == "secret_firewall:google_api_key"


def test_secret_firewall_catches_openai_and_google_key_shapes():
    firewall = SecretFirewall()
    assert firewall.inspect("AIza" + ("B" * 35)).category == "google_api_key"
    assert firewall.inspect("sk-proj-" + ("C" * 30)).category == "openai_api_key"


def test_restricted_material_is_minimized_before_cloud():
    raw = (
        "Contact alice@example.com or +1 (202) 555-0101. "
        "Local file: /Users/alice/private-project/report.txt"
    )
    decision = CloudEgressGate().prepare(raw, PrivacyMode.RESTRICTED)
    assert decision.action is EgressAction.SANITIZED
    assert "alice@example.com" not in decision.material
    assert "202" not in decision.material
    assert "/Users/alice" not in decision.material
    assert "<redacted:email>" in decision.material
    assert "<redacted:phone>" in decision.material
    assert "/Users/<redacted>" in decision.material
    assert set(decision.redactions) == {"email", "phone", "mac_user_path"}
    assert decision.original_sha256 != decision.material_sha256


def test_public_nonsecret_material_is_unchanged():
    raw = "Public open-source patch with no credentials."
    decision = CloudEgressGate().prepare(raw, PrivacyMode.PUBLIC)
    assert decision.action is EgressAction.ALLOW
    assert decision.material == raw
    assert decision.original_sha256 == decision.material_sha256


class FakeGeminiProvider:
    def __init__(self):
        self.calls = []

    def review(self, *, material, privacy, sanitized_for_egress):
        self.calls.append((material, privacy, sanitized_for_egress))
        return GeminiReviewResult(
            verdict="PASS",
            summary="ok",
            findings=(),
            model="fake-gemini",
            latency_seconds=0.001,
        )


def test_gemini_gateway_only_sends_minimized_restricted_material():
    provider = FakeGeminiProvider()
    gateway = GeminiReviewGateway(provider=provider)
    result = gateway.review(
        material="Review /Users/alice/repo. Contact alice@example.com.",
        privacy=PrivacyMode.RESTRICTED,
    )
    sent, privacy, sanitized = provider.calls[0]
    assert privacy is PrivacyMode.RESTRICTED
    assert sanitized is True
    assert "alice@example.com" not in sent
    assert "/Users/alice" not in sent
    assert result.egress.action is EgressAction.SANITIZED
    assert result.review.verdict == "PASS"


def test_gemini_gateway_denies_private_before_provider_call():
    provider = FakeGeminiProvider()
    gateway = GeminiReviewGateway(provider=provider)
    with pytest.raises(CloudEgressDenied, match="private_cloud_egress_denied"):
        gateway.review(material="private", privacy=PrivacyMode.PRIVATE)
    assert provider.calls == []
