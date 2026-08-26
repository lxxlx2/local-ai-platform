from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import pytest

from local_ai_control.services.codex_quota_guard import CodexQuotaSnapshot, quota_increases
from local_ai_control.services.generic_project_guarded import ManagedLocalCodexCommandRunner


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


def test_managed_runner_reaps_process_group_on_timeout(tmp_path: Path):
    pid_file = tmp_path / "pid.txt"
    runner = ManagedLocalCodexCommandRunner(tmp_path / "logs")
    started = time.monotonic()
    result = runner(
        ("/bin/sh", "-c", f"echo $$ > {pid_file}; sleep 30"),
        cwd=tmp_path,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        timeout=0.2,
        check=False,
    )
    assert result.returncode == 124
    assert time.monotonic() - started < 10
    pid = int(pid_file.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
