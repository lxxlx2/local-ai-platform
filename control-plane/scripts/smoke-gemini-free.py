#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

from local_ai_control.services.gemini_provider import GeminiReviewerProvider
from local_ai_control.services.provider_credentials import read_keychain_secret
from local_ai_control.services.provider_router import PrivacyMode


def main() -> int:
    os.environ["GEMINI_API_KEY"] = read_keychain_secret("gemini")
    try:
        review = GeminiReviewerProvider().review(
            material=(
                "File: calculator.py\n\n"
                "def add(a, b):\n"
                "    return a + b\n\n"
                "Review this tiny implementation for correctness."
            ),
            privacy=PrivacyMode.PUBLIC,
            sanitized_for_egress=True,
        )
        print(json.dumps({
            "status": "GEMINI_SMOKE_PASS",
            "model": review.model,
            "verdict": review.verdict,
            "findings": len(review.findings),
            "latency_seconds": review.latency_seconds,
        }, ensure_ascii=False))
        return 0
    finally:
        os.environ.pop("GEMINI_API_KEY", None)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({
            "status": "GEMINI_SMOKE_FAIL",
            "error_type": type(error).__name__,
            "message": str(error)[:240],
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
