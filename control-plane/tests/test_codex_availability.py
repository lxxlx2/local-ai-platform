from __future__ import annotations

import errno

from local_ai_control.services.codex_availability import (
    CodexAvailabilityEvidence,
    CodexAvailabilityMonitor,
    CodexAvailabilityStatus,
)
from local_ai_control.services.codex_quota_guard import CodexQuotaSnapshot


def snap(
    primary,
    secondary,
    primary_reset=1000,
    secondary_reset=2000,
    plan_type="plus",
):
    return CodexQuotaSnapshot(
        primary,
        primary_reset,
        secondary,
        secondary_reset,
        plan_type,
    )


def classify(**kwargs):
    return CodexAvailabilityMonitor().classify(
        CodexAvailabilityEvidence(**kwargs)
    )


def test_available_snapshot():
    assert classify(snapshot=snap(50, 10)) == CodexAvailabilityStatus.AVAILABLE


def test_primary_exhausted_quota():
    assert (
        classify(snapshot=snap(100, 10))
        == CodexAvailabilityStatus.QUOTA_EXHAUSTED
    )


def test_secondary_exhausted_quota():
    assert (
        classify(snapshot=snap(50, 100))
        == CodexAvailabilityStatus.QUOTA_EXHAUSTED
    )


def test_recognized_rate_limit_phrase():
    assert (
        classify(error_text="Too Many Requests")
        == CodexAvailabilityStatus.RATE_LIMITED
    )


def test_standalone_429_rate_limit():
    assert (
        classify(error_text="HTTP 429")
        == CodexAvailabilityStatus.RATE_LIMITED
    )


def test_provider_unavailable_text():
    assert (
        classify(error_text="Connection refused")
        == CodexAvailabilityStatus.PROVIDER_UNAVAILABLE
    )


def test_standalone_503_provider_unavailable():
    assert (
        classify(error_text="HTTP 503")
        == CodexAvailabilityStatus.PROVIDER_UNAVAILABLE
    )


def test_ambiguous_text_is_unknown():
    assert (
        classify(error_text="Something went wrong")
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_empty_text_is_unknown():
    assert classify(error_text="") == CodexAvailabilityStatus.UNKNOWN


def test_non_string_error_text_is_unknown():
    evidence = CodexAvailabilityEvidence(error_text=123)  # type: ignore[arg-type]
    assert (
        CodexAvailabilityMonitor().classify(evidence)
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_wrong_evidence_object_type_is_unknown():
    assert (
        CodexAvailabilityMonitor().classify("invalid")
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_multiple_evidence_sources_fail_closed():
    evidence = CodexAvailabilityEvidence(
        snapshot=snap(50, 10),
        error_text="429",
    )
    assert (
        CodexAvailabilityMonitor().classify(evidence)
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_negative_percentage_is_unknown():
    assert (
        classify(snapshot=snap(-1, 10))
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_percentage_over_100_is_unknown():
    assert (
        classify(snapshot=snap(101, 10))
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_boolean_percentage_is_unknown():
    assert (
        classify(snapshot=snap(True, 10))
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_negative_reset_timestamp_is_unknown():
    assert (
        classify(snapshot=snap(50, 10, primary_reset=-1))
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_boolean_reset_timestamp_is_unknown():
    assert (
        classify(snapshot=snap(50, 10, primary_reset=True))
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_empty_plan_type_is_unknown():
    assert (
        classify(snapshot=snap(50, 10, plan_type=""))
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_connection_error_is_provider_unavailable():
    assert (
        classify(error=ConnectionError("connection failed"))
        == CodexAvailabilityStatus.PROVIDER_UNAVAILABLE
    )


def test_timeout_error_is_provider_unavailable():
    assert (
        classify(error=TimeoutError("request timed out"))
        == CodexAvailabilityStatus.PROVIDER_UNAVAILABLE
    )


def test_known_network_errno_is_provider_unavailable():
    assert (
        classify(error=OSError(errno.ECONNREFUSED, "refused"))
        == CodexAvailabilityStatus.PROVIDER_UNAVAILABLE
    )


def test_arbitrary_oserror_is_unknown():
    assert (
        classify(error=OSError(errno.ENOENT, "missing"))
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_arbitrary_exception_is_unknown():
    assert (
        classify(error=RuntimeError("generic failure"))
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_embedded_4299_does_not_match():
    assert (
        classify(error_text="internal reference 4299")
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_embedded_15030_does_not_match_503():
    assert (
        classify(error_text="internal reference 15030")
        == CodexAvailabilityStatus.UNKNOWN
    )


def test_long_error_text_is_bounded():
    text = ("x" * CodexAvailabilityMonitor.MAX_ERROR_TEXT_CHARS) + " 429"
    assert classify(error_text=text) == CodexAvailabilityStatus.UNKNOWN


def test_account_wide_usage_change_is_not_task_attribution():
    monitor = CodexAvailabilityMonitor()
    before = snap(50, 10)
    after = snap(51, 10)

    assert monitor.is_task_attribution(before, after) is False
