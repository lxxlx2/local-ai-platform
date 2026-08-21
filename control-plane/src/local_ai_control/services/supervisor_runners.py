from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from local_ai_control.services.security import SecretFirewall
from .supervisor_contracts import (
    AI_ROOT, CONTROL_PLANE_PYTHON, CONTROL_PLANE_ROOT,
    ReviewFinding, ReviewResult, StageContext, StageResult, StageResultStatus,
    WorkflowStage, _bounded,
)

MAX_CANDIDATE_SCAN_BYTES = 1_000_000


class StaticPassRunner:
    def __init__(self, summary: str):
        self.summary = summary

    def run(self, context: StageContext) -> StageResult:
        return StageResult.passed(self.summary, metrics={"attempt": context.attempt})


class MockReviewRunner:
    """Deterministic demo contract: first review fails, second review passes."""

    def run(self, context: StageContext) -> StageResult:
        if not context.job.metadata.get("supervisor_demo"):
            return StageResult(StageResultStatus.BLOCKED, "Real reviewer not configured in V0.1", error="REVIEWER_NOT_CONFIGURED")
        if context.job.review_round == 0:
            return ReviewResult("FAIL", (ReviewFinding(
                "BLOCKING", "control-plane/tests/test_workflow_supervisor.py",
                "synthetic demo evidence", "synthetic demo revision",
            ),)).to_stage_result()
        return ReviewResult("PASS").to_stage_result()


class MockCodexRunner:
    def run(self, context: StageContext) -> StageResult:
        if not context.job.metadata.get("supervisor_demo"):
            return StageResult(StageResultStatus.BLOCKED, "Mock Codex runner is demo-only", error="DEMO_ONLY")
        metrics = {"mock": True}
        if context.stage is WorkflowStage.REVISION:
            findings = context.current_review_findings()
            if not findings:
                return StageResult(StageResultStatus.BLOCKED, "Revision findings unavailable", error="REVISION_FINDINGS_NOT_AVAILABLE")
            metrics["findings_consumed"] = len(findings)
        return StageResult.passed(f"Mock {context.stage.value.lower()} completed", metrics=metrics)


@dataclass(frozen=True)
class SafeCommandPolicy:
    python: Path = CONTROL_PLANE_PYTHON
    cwd_root: Path = CONTROL_PLANE_ROOT

    def validate(self, argv: Sequence[str], cwd: Path) -> tuple[str, ...]:
        command = tuple(str(item) for item in argv)
        resolved_cwd = Path(cwd).resolve()
        if not resolved_cwd.is_relative_to(self.cwd_root.resolve()):
            raise PermissionError("validation cwd outside control-plane")
        if len(command) < 3 or command[:3] != (str(self.python), "-m", "pytest"):
            raise PermissionError("command is not allowlisted pytest argv")
        for item in command[3:]:
            if item == "-q" or re.fullmatch(r"--maxfail=[1-9][0-9]*", item):
                continue
            candidate = (resolved_cwd / item.split("::", 1)[0]).resolve()
            if not candidate.is_relative_to((self.cwd_root / "tests").resolve()):
                raise PermissionError("pytest target outside tests directory")
        return command


class LocalValidationRunner:
    def __init__(self, argv: Sequence[str] | None = None, cwd: Path = CONTROL_PLANE_ROOT,
                 timeout_seconds: float = 120, policy: SafeCommandPolicy | None = None):
        self.argv = tuple(argv or (str(CONTROL_PLANE_PYTHON), "-m", "pytest", "-q"))
        self.cwd = Path(cwd)
        self.timeout_seconds = timeout_seconds
        self.policy = policy or SafeCommandPolicy()

    @staticmethod
    def _summary(stdout: str, stderr: str) -> str:
        combined = (stdout + "\n" + stderr).strip()
        if SecretFirewall().inspect(combined).action == "BLOCK":
            return "validation output redacted by Secret Firewall"
        return _bounded(combined, 4096) or "validation produced no output"

    def run(self, context: StageContext) -> StageResult:
        try:
            command = self.policy.validate(self.argv, self.cwd)
        except (PermissionError, ValueError) as error:
            return StageResult(StageResultStatus.BLOCKED, "Validation command denied", error=str(error))
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, cwd=self.cwd, capture_output=True, text=True, shell=False,
                timeout=min(self.timeout_seconds, context.timeout_seconds), check=False,
                env={"PATH": os.defpath, "PYTHONPATH": str(CONTROL_PLANE_ROOT / "src"), "PYTHONHASHSEED": "0"},
            )
        except subprocess.TimeoutExpired:
            return StageResult(StageResultStatus.TIMEOUT, "Validation timed out", error="TIMEOUT",
                               metrics={"duration_seconds": round(time.monotonic() - started, 3)})
        duration = round(time.monotonic() - started, 3)
        summary = self._summary(completed.stdout, completed.stderr)
        metrics = {"return_code": completed.returncode, "duration_seconds": duration,
                   "stdout_chars": len(completed.stdout), "stderr_chars": len(completed.stderr)}
        if completed.returncode == 0:
            return StageResult.passed(summary, metrics=metrics)
        return StageResult.failed(summary, error=f"pytest_exit_{completed.returncode}", metrics=metrics)


class SecurityRunner:
    """Fail-closed credential and isolation gate for changed/untracked candidates."""

    forbidden_tracked = re.compile(
        r"(^|/)(?:runtime|models|cache|tmp|inbox|output|logs)(/|$)|(?:\.sqlite3?|\.db|\.log|\.env|\.incomplete)$"
    )

    def __init__(self, repo_root: Path = AI_ROOT):
        self.repo_root = Path(repo_root).resolve()

    @staticmethod
    def _base_metrics(files_seen=0, files_scanned=0, files_blocked=0, oversized=0, binary=0) -> dict:
        return {
            "files_seen": files_seen,
            "files_scanned": files_scanned,
            "files_blocked": files_blocked,
            "oversized": oversized,
            "binary": binary,
        }

    def _scan_candidates(self, relatives: Sequence[str]) -> StageResult:
        firewall = SecretFirewall()
        metrics = self._base_metrics()
        for relative in sorted(set(relatives)):
            if not relative:
                continue
            metrics["files_seen"] += 1
            raw = self.repo_root / relative
            try:
                if raw.is_symlink():
                    metrics["files_blocked"] += 1
                    return StageResult.failed("Candidate symlink is not scan-safe", error="UNSCANNABLE_CANDIDATE", metrics=metrics)
                path = raw.resolve(strict=True)
                if not path.is_relative_to(self.repo_root) or not path.is_file():
                    metrics["files_blocked"] += 1
                    return StageResult.failed("Candidate path is outside scan scope", error="UNSCANNABLE_CANDIDATE", metrics=metrics)
                size = path.stat().st_size
            except OSError:
                metrics["files_blocked"] += 1
                return StageResult.failed("Candidate file cannot be inspected", error="UNSCANNABLE_CANDIDATE", metrics=metrics)
            if size > MAX_CANDIDATE_SCAN_BYTES:
                metrics["oversized"] += 1
                metrics["files_blocked"] += 1
                return StageResult.failed(
                    "Oversized candidate requires explicit review",
                    error="OVERSIZED_UNSCANNED_CANDIDATE",
                    metrics=metrics,
                )
            try:
                data = path.read_bytes()
            except OSError:
                metrics["files_blocked"] += 1
                return StageResult.failed("Candidate file cannot be read", error="UNSCANNABLE_CANDIDATE", metrics=metrics)
            if b"\x00" in data:
                metrics["binary"] += 1
                metrics["files_blocked"] += 1
                return StageResult.failed("Binary candidate is not approved for V0.1", error="BINARY_CANDIDATE_UNAPPROVED", metrics=metrics)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                metrics["binary"] += 1
                metrics["files_blocked"] += 1
                return StageResult.failed("Non UTF-8 candidate is not scan-safe", error="BINARY_CANDIDATE_UNAPPROVED", metrics=metrics)
            metrics["files_scanned"] += 1
            if firewall.inspect(text).action == "BLOCK":
                metrics["files_blocked"] += 1
                return StageResult.failed("Credential scan failed", error="SECRET_SCAN", metrics=metrics)
        return StageResult.passed("Candidate credential scan passed", metrics=metrics)

    def _run_isolation_regression(self, context: StageContext) -> StageResult:
        return LocalValidationRunner(
            (str(CONTROL_PLANE_PYTHON), "-m", "pytest", "-q", "tests/test_gateway_v02.py", "tests/test_control.py"),
            timeout_seconds=60,
        ).run(context)

    def run(self, context: StageContext) -> StageResult:
        if self.repo_root != AI_ROOT.resolve():
            return StageResult(StageResultStatus.BLOCKED, "Security scope denied", error="PATH_SCOPE")
        tracked = subprocess.run(["git", "ls-files"], cwd=self.repo_root, capture_output=True, text=True,
                                 shell=False, timeout=10, check=False)
        if tracked.returncode != 0:
            return StageResult.failed("Unable to enumerate tracked files", error="GIT_LS_FILES")
        forbidden = [line for line in tracked.stdout.splitlines() if self.forbidden_tracked.search(line)]
        if forbidden:
            return StageResult.failed("Tracked runtime/secret policy failed", error="FORBIDDEN_TRACKED_FILE",
                                      metrics=self._base_metrics(files_blocked=len(forbidden)))
        changed = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=self.repo_root,
                                 capture_output=True, text=True, shell=False, timeout=10, check=False)
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=self.repo_root,
                                   capture_output=True, text=True, shell=False, timeout=10, check=False)
        if changed.returncode != 0 or untracked.returncode != 0:
            return StageResult.failed("Unable to enumerate candidate files", error="GIT_CANDIDATE_FILES")
        scan = self._scan_candidates(changed.stdout.splitlines() + untracked.stdout.splitlines())
        if scan.status is not StageResultStatus.PASS:
            return scan
        isolation = self._run_isolation_regression(context)
        if isolation.status is not StageResultStatus.PASS:
            return StageResult.failed(
                "Security isolation regression failed", error="SECURITY_REGRESSION",
                metrics=scan.metrics | {"isolation_return_code": isolation.metrics.get("return_code")},
            )
        return StageResult.passed(
            "Security policies and isolation regressions passed",
            metrics=scan.metrics | {"forbidden_count": 0, "isolation_return_code": isolation.metrics.get("return_code")},
        )


class GitGateRunner:
    """Read-only production gate. It never commits, pushes, or merges."""

    def __init__(self, repo_root: Path = AI_ROOT):
        self.repo_root = Path(repo_root).resolve()

    def run(self, context: StageContext) -> StageResult:
        completed = {
            row["stage"] for row in context.repository.db.execute(
                "SELECT stage FROM supervisor_stage_runs WHERE job_id=? AND status='PASS'", (context.job.job_id,)
            ).fetchall()
        }
        required = {WorkflowStage.VALIDATION.value, WorkflowStage.REVIEW.value, WorkflowStage.SECURITY.value}
        missing = sorted(required - completed)
        if missing:
            return StageResult(StageResultStatus.BLOCKED, "Git Gate prerequisites are incomplete",
                               error="GIT_GATE_PREREQUISITES", metrics={"missing_stages": missing})
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=self.repo_root,
                                capture_output=True, text=True, shell=False, timeout=5, check=False).stdout.strip()
        if not branch or branch == "main":
            return StageResult(StageResultStatus.BLOCKED, "Git Gate requires a feature branch", error="MAIN_BRANCH_DENIED")
        return StageResult.passed(
            "Git Gate policy satisfied; V0.1 performs no Git mutation",
            metrics={"branch": branch, "git_mutation": False, "review_pending": True},
        )
