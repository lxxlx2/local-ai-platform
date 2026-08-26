from __future__ import annotations

from local_ai_control.services.codex_quota_guard import CodexQuotaSnapshot, quota_increases


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
