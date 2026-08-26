from __future__ import annotations

import pytest

from local_ai_control.services.codex_quota_guard import (
    CodexQuotaGuard,
    CodexQuotaProbeError,
    CodexQuotaSnapshot,
    quota_increases,
)


def snap(primary, secondary, primary_reset=1000, secondary_reset=2000):
    return CodexQuotaSnapshot(primary, primary_reset, secondary, secondary_reset, "plus")


def test_quota_guard_accepts_unchanged_account_usage():
    assert quota_increases(snap(51, 8), snap(51, 8)) == ()


def test_quota_guard_detects_primary_or_weekly_increase():
    assert quota_increases(snap(51, 8), snap(52, 8)) == ("primary",)
    assert quota_increases(snap(51, 8), snap(51, 9)) == ("secondary",)


def test_quota_guard_treats_new_window_with_usage_as_suspicious():
    changed = quota_increases(snap(51, 8), snap(1, 8, primary_reset=3000))
    assert changed == ("primary_window_changed_with_usage",)


def test_quota_guard_retries_transient_probe_failure():
    calls = []
    sleeps = []

    def snapshotter():
        calls.append(True)
        if len(calls) == 1:
            raise CodexQuotaProbeError("temporary app-server handshake failure")
        return snap(51, 8)

    guard = CodexQuotaGuard(
        snapshotter,
        attempts=3,
        retry_delay_seconds=0.25,
        sleeper=sleeps.append,
    )
    assert guard.before() == snap(51, 8)
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_quota_guard_remains_fail_closed_after_bounded_retries():
    calls = []

    def snapshotter():
        calls.append(True)
        raise CodexQuotaProbeError("still unavailable")

    guard = CodexQuotaGuard(
        snapshotter,
        attempts=3,
        retry_delay_seconds=0,
    )
    with pytest.raises(CodexQuotaProbeError, match="failed after 3 attempts"):
        guard.before()
    assert len(calls) == 3
