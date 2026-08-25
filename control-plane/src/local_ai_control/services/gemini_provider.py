from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import os
import time
from typing import Any, Callable, Mapping

from .provider_router import PrivacyMode


DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"
DEFAULT_GEMINI_FALLBACK_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash")
MIN_GEMINI_SDK_MAJOR = 2
DEFAULT_GEMINI_TIMEOUT_MS = 35_000


class GeminiProviderError(RuntimeError):
    category = "INTERNAL_ERROR"


class GeminiAuthError(GeminiProviderError):
    category = "AUTH_ERROR"


class GeminiRateLimitError(GeminiProviderError):
    category = "RATE_LIMIT"


class GeminiTimeoutError(GeminiProviderError):
    category = "TIMEOUT"


class GeminiModelUnavailableError(GeminiProviderError):
    category = "MODEL_UNAVAILABLE"


class GeminiInvalidOutputError(GeminiProviderError):
    category = "INVALID_OUTPUT"


class GeminiPrivacyDenied(GeminiProviderError):
    category = "PRIVACY_DENIED"


class GeminiBadRequestError(GeminiProviderError):
    category = "BAD_REQUEST"


class GeminiSdkVersionError(GeminiProviderError):
    category = "SDK_VERSION"


@dataclass(frozen=True)
class GeminiReviewFinding:
    severity: str
    scope: str
    file: str | None
    evidence: str
    recommended_fix: str


@dataclass(frozen=True)
class GeminiReviewResult:
    verdict: str
    summary: str
    findings: tuple[GeminiReviewFinding, ...]
    model: str
    latency_seconds: float


Transport = Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]


REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "NEEDS_CHANGES"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["BLOCKING", "HIGH", "MEDIUM", "LOW"]},
                    "scope": {"type": "string", "enum": ["FILE", "WORKFLOW"]},
                    "file": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "evidence": {"type": "string"},
                    "recommended_fix": {"type": "string"},
                },
                "required": ["severity", "scope", "file", "evidence", "recommended_fix"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdict", "summary", "findings"],
    "additionalProperties": False,
}


def _safe_remote_error(error: Exception, api_key: str) -> str:
    message = str(error).replace(api_key, "<redacted>")
    message = " ".join(message.split())
    return message[:500] or type(error).__name__


def _require_supported_sdk() -> str:
    try:
        installed = package_version("google-genai")
    except PackageNotFoundError as error:
        raise GeminiProviderError("google-genai SDK is not installed") from error
    try:
        major = int(installed.split(".", 1)[0])
    except (TypeError, ValueError) as error:
        raise GeminiSdkVersionError(f"unrecognized google-genai version: {installed}") from error
    if major < MIN_GEMINI_SDK_MAJOR:
        raise GeminiSdkVersionError(
            f"google-genai>={MIN_GEMINI_SDK_MAJOR}.0.0 required; installed={installed}"
        )
    return installed


class GeminiReviewerProvider:
    """Read-only Gemini reviewer using Generate Content.

    The normal path prefers Gemini 3.7 Flash, but free-tier/server saturation must not
    block the workflow. Transient timeout/504/429/model-unavailable errors fall back
    to other stable Flash models that also have a free tier. Authentication, privacy,
    schema and bad-request errors fail closed and are never masked by fallback.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_GEMINI_MODEL,
        fallback_models: tuple[str, ...] = DEFAULT_GEMINI_FALLBACK_MODELS,
        transport: Transport | None = None,
    ):
        self.model = model
        self.fallback_models = tuple(item for item in fallback_models if item != model)
        self._uses_official_transport = transport is None
        self.transport = transport or self._official_transport

    @staticmethod
    def _official_transport(model: str, prompt: str, schema: Mapping[str, Any]) -> Mapping[str, Any]:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise GeminiAuthError("GEMINI_API_KEY is not configured")
        _require_supported_sdk()
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as error:
            raise GeminiProviderError("google-genai SDK is not installed") from error

        client = genai.Client(
            api_key=key,
            http_options={"timeout": DEFAULT_GEMINI_TIMEOUT_MS},
        )
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=dict(schema),
                    max_output_tokens=1536,
                    temperature=0,
                    thinking_config=types.ThinkingConfig(thinking_level="low"),
                ),
            )
            text = response.text
        except TimeoutError as error:
            raise GeminiTimeoutError("Gemini request timed out") from error
        except Exception as error:
            message = _safe_remote_error(error, key)
            lowered = message.lower()
            if (
                "timeout" in lowered
                or "timed out" in lowered
                or "504" in lowered
                or "deadline_exceeded" in lowered
                or "deadline expired" in lowered
            ):
                raise GeminiTimeoutError(message) from error
            if "429" in lowered or "resource_exhausted" in lowered or "rate limit" in lowered:
                raise GeminiRateLimitError(message) from error
            if "401" in lowered or "403" in lowered or "unauth" in lowered or ("api key" in lowered and "invalid" in lowered):
                raise GeminiAuthError(message) from error
            if "404" in lowered or "not found" in lowered or ("model" in lowered and "unavailable" in lowered):
                raise GeminiModelUnavailableError(message) from error
            if "400" in lowered or "badrequest" in lowered or "invalid_argument" in lowered or "validation error" in lowered:
                raise GeminiBadRequestError(message) from error
            raise GeminiProviderError(f"{type(error).__name__}: {message}") from error
        finally:
            try:
                client.close()
            except Exception:
                pass

        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError) as error:
            raise GeminiInvalidOutputError("Gemini returned invalid structured output") from error
        if not isinstance(payload, dict):
            raise GeminiInvalidOutputError("Gemini structured output is not an object")
        return payload

    @staticmethod
    def _validate_payload(payload: Mapping[str, Any]) -> tuple[str, str, tuple[GeminiReviewFinding, ...]]:
        verdict = payload.get("verdict")
        summary = payload.get("summary")
        raw_findings = payload.get("findings")
        if verdict not in {"PASS", "NEEDS_CHANGES"} or not isinstance(summary, str) or not isinstance(raw_findings, list):
            raise GeminiInvalidOutputError("Gemini review schema mismatch")
        findings = []
        for raw in raw_findings:
            if not isinstance(raw, dict):
                raise GeminiInvalidOutputError("Gemini finding is not an object")
            severity = raw.get("severity")
            scope = raw.get("scope")
            file_name = raw.get("file")
            evidence = raw.get("evidence")
            recommended_fix = raw.get("recommended_fix")
            if severity not in {"BLOCKING", "HIGH", "MEDIUM", "LOW"}:
                raise GeminiInvalidOutputError("invalid Gemini finding severity")
            if scope not in {"FILE", "WORKFLOW"}:
                raise GeminiInvalidOutputError("invalid Gemini finding scope")
            if file_name is not None and not isinstance(file_name, str):
                raise GeminiInvalidOutputError("invalid Gemini finding file")
            if not isinstance(evidence, str) or not isinstance(recommended_fix, str):
                raise GeminiInvalidOutputError("invalid Gemini finding body")
            findings.append(GeminiReviewFinding(severity, scope, file_name, evidence, recommended_fix))
        if verdict == "PASS" and findings:
            raise GeminiInvalidOutputError("PASS review cannot contain findings")
        if verdict == "NEEDS_CHANGES" and not findings:
            raise GeminiInvalidOutputError("NEEDS_CHANGES requires findings")
        return verdict, summary, tuple(findings)

    def review(self, *, material: str, privacy: PrivacyMode, sanitized_for_egress: bool) -> GeminiReviewResult:
        if privacy is PrivacyMode.PRIVATE:
            raise GeminiPrivacyDenied("PRIVATE material cannot be sent to Gemini")
        if privacy is PrivacyMode.RESTRICTED and not sanitized_for_egress:
            raise GeminiPrivacyDenied("RESTRICTED material must pass the egress gate")
        if not isinstance(material, str) or not material.strip():
            raise ValueError("review material is empty")
        if len(material.encode("utf-8")) > 256_000:
            raise ValueError("review material exceeds bounded Gemini egress size")

        prompt = (
            "You are an independent read-only software reviewer. Review only the supplied material. "
            "Do not assume shell, filesystem, Git, deployment or secret access. Return findings grounded in the material.\n\n"
            + material
        )

        models = (self.model, *self.fallback_models) if self._uses_official_transport else (self.model,)
        started = time.monotonic()
        last_transient: Exception | None = None
        for model in models:
            try:
                payload = self.transport(model, prompt, REVIEW_SCHEMA)
                verdict, summary, findings = self._validate_payload(payload)
                return GeminiReviewResult(
                    verdict=verdict,
                    summary=summary,
                    findings=findings,
                    model=model,
                    latency_seconds=round(time.monotonic() - started, 3),
                )
            except (GeminiTimeoutError, GeminiRateLimitError, GeminiModelUnavailableError) as error:
                last_transient = error
                continue

        if last_transient is not None:
            raise last_transient
        raise GeminiProviderError("Gemini reviewer exhausted provider candidates")
