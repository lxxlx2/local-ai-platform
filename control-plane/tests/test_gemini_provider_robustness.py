from __future__ import annotations

import pytest

from local_ai_control.services.gemini_provider import (
    GeminiInvalidOutputError,
    GeminiReviewerProvider,
)
from local_ai_control.services.provider_router import PrivacyMode


VALID_PASS = {
    "verdict": "PASS",
    "summary": "review passed",
    "findings": [],
}


def test_invalid_structured_output_uses_bounded_fallback():
    calls = []

    def transport(model, prompt, schema):
        calls.append(model)
        if model == "model-primary":
            raise GeminiInvalidOutputError("truncated response")
        return VALID_PASS

    provider = GeminiReviewerProvider(
        model="model-primary",
        fallback_models=("model-fallback",),
    )

    provider.transport = transport

    result = provider.review(
        material="bounded review material",
        privacy=PrivacyMode.PUBLIC,
        sanitized_for_egress=True,
    )

    assert calls == ["model-primary", "model-fallback"]
    assert result.model == "model-fallback"
    assert result.verdict == "PASS"


def test_all_invalid_structured_outputs_fail_closed():
    calls = []

    def transport(model, prompt, schema):
        calls.append(model)
        raise GeminiInvalidOutputError("truncated response")

    provider = GeminiReviewerProvider(
        model="model-primary",
        fallback_models=("model-fallback",),
    )

    provider.transport = transport

    with pytest.raises(GeminiInvalidOutputError):
        provider.review(
            material="bounded review material",
            privacy=PrivacyMode.PUBLIC,
            sanitized_for_egress=True,
        )

    assert calls == ["model-primary", "model-fallback"]


def test_review_prompt_requests_bounded_findings():
    captured = {}

    def transport(model, prompt, schema):
        captured["prompt"] = prompt
        return VALID_PASS

    provider = GeminiReviewerProvider(
        model="model-primary",
        transport=transport,
    )

    result = provider.review(
        material="review this",
        privacy=PrivacyMode.PUBLIC,
        sanitized_for_egress=True,
    )

    assert result.verdict == "PASS"
    assert "no more than 8 findings" in captured["prompt"]


def test_provider_error_subclasses_are_not_genericized():
    error = GeminiInvalidOutputError(
        "Gemini structured output exceeded output token budget"
    )

    assert isinstance(error, GeminiInvalidOutputError)
    assert error.category == "INVALID_OUTPUT"


def test_sanitized_review_prompt_marks_redactions_as_synthetic():
    captured = {}

    def transport(model, prompt, schema):
        captured["prompt"] = prompt
        return VALID_PASS

    provider = GeminiReviewerProvider(
        model="model-primary",
        transport=transport,
    )

    provider.review(
        material='path="/Users/<redacted>/project"',
        privacy=PrivacyMode.RESTRICTED,
        sanitized_for_egress=True,
    )

    prompt = captured["prompt"]

    assert "PRIVACY SANITIZATION NOTICE" in prompt
    assert "synthetic placeholders" in prompt
    assert "not literal repository source text" in prompt


def test_bounded_review_does_not_equate_omission_with_missing():
    captured = {}

    def transport(model, prompt, schema):
        captured["prompt"] = prompt
        return VALID_PASS

    provider = GeminiReviewerProvider(
        model="model-primary",
        transport=transport,
    )

    provider.review(
        material="references scripts/helper.sh",
        privacy=PrivacyMode.PUBLIC,
        sanitized_for_egress=False,
    )

    prompt = captured["prompt"]

    assert "UNVERIFIED, not MISSING" in prompt
    assert "unless the supplied evidence explicitly proves" in prompt
