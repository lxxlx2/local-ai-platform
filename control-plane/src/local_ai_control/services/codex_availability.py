from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import errno
import re

from local_ai_control.services.codex_quota_guard import (
    CodexQuotaProbeError,
    CodexQuotaSnapshot,
)


class CodexAvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CodexAvailabilityEvidence:
    snapshot: CodexQuotaSnapshot | None = None
    error: Exception | None = None
    error_text: str | None = None


class CodexAvailabilityMonitor:
    """Deterministic, fail-closed classification of Codex availability."""

    MAX_ERROR_TEXT_CHARS = 1024

    _QUOTA_EXHAUSTED_MARKERS = (
        "quota exhausted",
        "quota_exhausted",
        "usage limit reached",
        "usage_limit_reached",
    )

    _RATE_LIMIT_MARKERS = (
        "rate limit",
        "rate_limit",
        "too many requests",
    )

    _PROVIDER_UNAVAILABLE_MARKERS = (
        "connection refused",
        "connection reset",
        "connection timed out",
        "provider unavailable",
        "service unavailable",
        "dns resolution failed",
        "network unreachable",
    )

    _HTTP_STATUS_RE = re.compile(r"(?<!\d)(429|502|503)(?!\d)")

    _PROVIDER_ERRNOS = frozenset(
        {
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ETIMEDOUT,
            errno.EHOSTUNREACH,
            errno.ENETUNREACH,
        }
    )

    def classify(self, evidence: object) -> CodexAvailabilityStatus:
        if not isinstance(evidence, CodexAvailabilityEvidence):
            return CodexAvailabilityStatus.UNKNOWN

        supplied = sum(
            value is not None
            for value in (evidence.snapshot, evidence.error, evidence.error_text)
        )
        if supplied != 1:
            return CodexAvailabilityStatus.UNKNOWN

        if evidence.error is not None:
            return self._classify_error(evidence.error)

        if evidence.error_text is not None:
            return self._classify_error_text(evidence.error_text)

        return self._classify_snapshot(evidence.snapshot)

    def _classify_error(self, error: object) -> CodexAvailabilityStatus:
        if not isinstance(error, Exception):
            return CodexAvailabilityStatus.UNKNOWN

        if isinstance(error, (ConnectionError, TimeoutError)):
            return CodexAvailabilityStatus.PROVIDER_UNAVAILABLE

        if isinstance(error, OSError) and error.errno in self._PROVIDER_ERRNOS:
            return CodexAvailabilityStatus.PROVIDER_UNAVAILABLE

        if isinstance(error, CodexQuotaProbeError):
            return self._classify_error_text(str(error))

        return CodexAvailabilityStatus.UNKNOWN

    def _classify_error_text(self, text: object) -> CodexAvailabilityStatus:
        if not isinstance(text, str):
            return CodexAvailabilityStatus.UNKNOWN

        normalized = text[: self.MAX_ERROR_TEXT_CHARS].lower().strip()
        if not normalized:
            return CodexAvailabilityStatus.UNKNOWN

        for marker in self._QUOTA_EXHAUSTED_MARKERS:
            if marker in normalized:
                return CodexAvailabilityStatus.QUOTA_EXHAUSTED

        for marker in self._RATE_LIMIT_MARKERS:
            if marker in normalized:
                return CodexAvailabilityStatus.RATE_LIMITED

        for marker in self._PROVIDER_UNAVAILABLE_MARKERS:
            if marker in normalized:
                return CodexAvailabilityStatus.PROVIDER_UNAVAILABLE

        status_match = self._HTTP_STATUS_RE.search(normalized)
        if status_match:
            status = status_match.group(1)
            if status == "429":
                return CodexAvailabilityStatus.RATE_LIMITED
            if status in {"502", "503"}:
                return CodexAvailabilityStatus.PROVIDER_UNAVAILABLE

        return CodexAvailabilityStatus.UNKNOWN

    @staticmethod
    def _valid_int(value: object, *, minimum: int, maximum: int | None = None) -> bool:
        if type(value) is not int:
            return False
        if value < minimum:
            return False
        if maximum is not None and value > maximum:
            return False
        return True

    def _classify_snapshot(self, snapshot: object) -> CodexAvailabilityStatus:
        if not isinstance(snapshot, CodexQuotaSnapshot):
            return CodexAvailabilityStatus.UNKNOWN

        if not self._valid_int(
            snapshot.primary_used_percent,
            minimum=0,
            maximum=100,
        ):
            return CodexAvailabilityStatus.UNKNOWN

        if not self._valid_int(
            snapshot.secondary_used_percent,
            minimum=0,
            maximum=100,
        ):
            return CodexAvailabilityStatus.UNKNOWN

        if not self._valid_int(snapshot.primary_resets_at, minimum=0):
            return CodexAvailabilityStatus.UNKNOWN

        if not self._valid_int(snapshot.secondary_resets_at, minimum=0):
            return CodexAvailabilityStatus.UNKNOWN

        if not isinstance(snapshot.plan_type, str) or not snapshot.plan_type.strip():
            return CodexAvailabilityStatus.UNKNOWN

        if (
            snapshot.primary_used_percent == 100
            or snapshot.secondary_used_percent == 100
        ):
            return CodexAvailabilityStatus.QUOTA_EXHAUSTED

        return CodexAvailabilityStatus.AVAILABLE

    @staticmethod
    def is_task_attribution(
        before: CodexQuotaSnapshot,
        after: CodexQuotaSnapshot,
    ) -> bool:
        """Account-wide quota telemetry alone never proves task attribution."""
        return False
