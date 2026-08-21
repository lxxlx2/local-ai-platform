import signal
import subprocess
from pathlib import Path

from local_ai_control.services.supervisor import (
    AI_ROOT, SecurityRunner, StageContext, StageResult, StageResultStatus, WorkflowStage,
)
from local_ai_control.supervisor import process_identity
import local_ai_control.services.supervisor_round2_security as round2_security


def test_process_cleanup_expected_child_dead_reused_and_success(monkeypatch, tmp_path):
    exact = process_identity.ProcessIdentity(123, str(process_identity.CONTROL_PLANE_PYTHON),
                                             process_identity.EXPECTED_ARGV, "START-1")
    identity = tmp_path / "identity.json"
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: None)
    assert process_identity.cleanup_started_process(123, "START-1", identity) == "ALREADY_DEAD"
    reused = process_identity.ProcessIdentity(123, "/other", ("other",), "START-2")
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: reused)
    kills = []; monkeypatch.setattr(process_identity.os, "kill", lambda *args: kills.append(args))
    assert process_identity.cleanup_started_process(123, "START-1", identity) == "ORPHAN_RECONCILIATION_REQUIRED"
    assert kills == []
    snapshots = iter((exact, None))
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: next(snapshots, None))
    assert process_identity.cleanup_started_process(123, "START-1", identity, 0.2) == "TERMINATED"
    assert kills[-1] == (123, signal.SIGTERM)


def test_start_script_capture_failure_has_exact_child_cleanup_and_no_broad_kill():
    source = (AI_ROOT / "control-plane/scripts/start-supervisor.sh").read_text()
    assert "start-identity" in source and "cleanup-start" in source
    assert "ORPHAN_RECONCILIATION_REQUIRED" in source
    assert "pkill" not in source and "killall" not in source and "kill -9" not in source


def init_git(root: Path):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def git_commit_all(root: Path):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)


def security_context(tmp_path):
    return StageContext(
        type("Job", (), {"job_id": "security", "owner_id": "owner", "review_round": 0})(),
        WorkflowStage.SECURITY, 1, "security", 30, None,
    )


def test_security_tracked_deletion_passes_and_arbitrary_missing_still_fails(tmp_path, monkeypatch):
    root = tmp_path / "repo"; root.mkdir(); init_git(root)
    target = root / "old.txt"; target.write_text("clean"); git_commit_all(root); target.unlink()
    monkeypatch.setattr(round2_security, "AI_ROOT", root)
    runner = SecurityRunner(root)
    monkeypatch.setattr(runner, "_run_isolation_regression", lambda _ctx: StageResult.passed("ok", metrics={"return_code": 0}))
    result = runner.run(security_context(tmp_path))
    assert result.status is StageResultStatus.PASS and result.metrics["deleted"] == 1
    missing = runner._scan_candidates(["arbitrary-missing.txt"])
    assert missing.status is StageResultStatus.FAIL and missing.error == "UNSCANNABLE_CANDIDATE"


def test_security_modified_oversized_and_new_binary_fail_closed(tmp_path, monkeypatch):
    root = tmp_path / "repo"; root.mkdir(); init_git(root)
    tracked = root / "tracked.txt"; tracked.write_text("clean"); git_commit_all(root)
    monkeypatch.setattr(round2_security, "AI_ROOT", root)
    runner = SecurityRunner(root)
    monkeypatch.setattr(runner, "_run_isolation_regression", lambda _ctx: StageResult.passed("ok", metrics={"return_code": 0}))
    tracked.write_bytes(b"a" * 1_000_001)
    assert runner.run(security_context(tmp_path)).error == "OVERSIZED_UNSCANNED_CANDIDATE"
    tracked.write_text("clean"); subprocess.run(["git", "checkout", "--", "tracked.txt"], cwd=root, check=True)
    (root / "new.bin").write_bytes(b"a\x00b")
    assert runner.run(security_context(tmp_path)).error == "BINARY_CANDIDATE_UNAPPROVED"


def test_security_rename_scans_new_path_and_reports_metric(tmp_path, monkeypatch):
    root = tmp_path / "repo"; root.mkdir(); init_git(root)
    old = root / "old.txt"; old.write_text("clean"); git_commit_all(root)
    subprocess.run(["git", "mv", "old.txt", "new.txt"], cwd=root, check=True)
    monkeypatch.setattr(round2_security, "AI_ROOT", root)
    runner = SecurityRunner(root)
    monkeypatch.setattr(runner, "_run_isolation_regression", lambda _ctx: StageResult.passed("ok", metrics={"return_code": 0}))
    result = runner.run(security_context(tmp_path))
    assert result.status is StageResultStatus.PASS and result.metrics["renamed"] == 1
