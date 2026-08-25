from __future__ import annotations

import os
import threading

from mcp.server import MCPServer

from .cloud_egress import CloudEgressDenied
from .gemini_provider import GeminiProviderError
from .gemini_review_gateway import GeminiReviewGateway
from .provider_credentials import ProviderCredentialError, read_keychain_secret
from .provider_router import PrivacyMode


mcp = MCPServer("Local Gemini Reviewer")
_KEYCHAIN_ENV_LOCK = threading.Lock()


def _privacy(value: str) -> PrivacyMode:
    try:
        return PrivacyMode(str(value).upper())
    except ValueError as error:
        raise ValueError("privacy must be PUBLIC, RESTRICTED, or PRIVATE") from error


def _review_with_keychain(material: str, privacy: PrivacyMode):
    with _KEYCHAIN_ENV_LOCK:
        previous = os.environ.get("GEMINI_API_KEY")
        inserted = previous is None
        if inserted:
            os.environ["GEMINI_API_KEY"] = read_keychain_secret("gemini")
        try:
            return GeminiReviewGateway().review(material=material, privacy=privacy)
        finally:
            if inserted:
                os.environ.pop("GEMINI_API_KEY", None)


@mcp.tool()
def gemini_review(material: str, privacy: str = "RESTRICTED") -> dict:
    """Read-only Gemini review behind the local cloud-egress policy.

    PUBLIC may leave the Mac unchanged after secret scanning. RESTRICTED is
    minimized before egress. PRIVATE is denied and never sent to Gemini.
    This tool grants no shell, filesystem, Git, download, credential, or service
    authority to Gemini.
    """
    mode = _privacy(privacy)
    try:
        result = _review_with_keychain(material, mode)
    except CloudEgressDenied as error:
        return {"status": "DENIED", "reason": str(error), "privacy": mode.value}
    except ProviderCredentialError:
        return {"status": "UNAVAILABLE", "reason": "GEMINI_CREDENTIAL_UNAVAILABLE"}
    except GeminiProviderError as error:
        return {
            "status": "UNAVAILABLE",
            "reason": type(error).__name__,
            "privacy": mode.value,
        }

    return {
        "status": "OK",
        "verdict": result.review.verdict,
        "summary": result.review.summary,
        "findings": [
            {
                "severity": item.severity,
                "scope": item.scope,
                "file": item.file,
                "evidence": item.evidence,
                "recommended_fix": item.recommended_fix,
            }
            for item in result.review.findings
        ],
        "model": result.review.model,
        "latency_seconds": result.review.latency_seconds,
        "privacy": result.egress.privacy.value,
        "redactions": list(result.egress.redactions),
        "egress_material_sha256": result.egress.material_sha256,
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
