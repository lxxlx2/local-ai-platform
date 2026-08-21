from __future__ import annotations

import subprocess
from .supervisor_contracts import AI_ROOT, StageContext, StageResult, StageResultStatus
from .supervisor_runners import SecurityRunner as BaseSecurityRunner

class Round2SecurityRunner(BaseSecurityRunner):
    @staticmethod
    def _base_metrics(files_seen=0, files_scanned=0, files_blocked=0, oversized=0, binary=0,
                      deleted=0, renamed=0) -> dict:
        return {
            "files_seen": files_seen, "files_scanned": files_scanned, "files_blocked": files_blocked,
            "oversized": oversized, "binary": binary, "deleted": deleted, "renamed": renamed,
        }

    def _tracked_in_head(self, relative: str) -> bool:
        result = subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], cwd=self.repo_root,
                                capture_output=True, text=True, shell=False, timeout=5, check=False)
        return result.returncode == 0

    def _parse_changed(self, text: str):
        tokens = text.split("\0")
        index = 0
        scan, deleted, renamed = [], [], []
        while index < len(tokens):
            status = tokens[index]
            index += 1
            if not status:
                continue
            code = status[0]
            if code in {"R", "C"}:
                if index + 1 >= len(tokens):
                    raise ValueError("malformed rename status")
                old, new = tokens[index], tokens[index + 1]
                index += 2
                if not old or not new:
                    raise ValueError("malformed rename status")
                renamed.append((old, new))
                scan.append(new)
            else:
                if index >= len(tokens):
                    raise ValueError("malformed change status")
                path = tokens[index]
                index += 1
                if code == "D":
                    deleted.append(path)
                elif code in {"A", "M", "T", "U"}:
                    scan.append(path)
                else:
                    raise ValueError(f"unsupported git status {status}")
        return scan, deleted, renamed

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
        changed = subprocess.run(["git", "diff", "--name-status", "-z", "HEAD"], cwd=self.repo_root,
                                 capture_output=True, text=True, shell=False, timeout=10, check=False)
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=self.repo_root,
                                   capture_output=True, text=True, shell=False, timeout=10, check=False)
        if changed.returncode != 0 or untracked.returncode != 0:
            return StageResult.failed("Unable to enumerate candidate files", error="GIT_CANDIDATE_FILES")
        try:
            scan_paths, deletions, renames = self._parse_changed(changed.stdout)
        except ValueError:
            return StageResult.failed("Unable to parse candidate file status", error="GIT_CANDIDATE_FILES")
        for relative in deletions:
            candidate = (self.repo_root / relative).resolve()
            if not candidate.is_relative_to(self.repo_root) or not self._tracked_in_head(relative):
                return StageResult.failed("Deletion path is not a tracked in-scope file", error="UNSCANNABLE_CANDIDATE")
        scan = self._scan_candidates(scan_paths + [x for x in untracked.stdout.split("\0") if x])
        if scan.status is not StageResultStatus.PASS:
            scan.metrics.update({"deleted": len(deletions), "renamed": len(renames)})
            return scan
        metrics = scan.metrics | {"deleted": len(deletions), "renamed": len(renames)}
        isolation = self._run_isolation_regression(context)
        if isolation.status is not StageResultStatus.PASS:
            return StageResult.failed("Security isolation regression failed", error="SECURITY_REGRESSION",
                                      metrics=metrics | {"isolation_return_code": isolation.metrics.get("return_code")})
        return StageResult.passed("Security policies and isolation regressions passed",
                                  metrics=metrics | {"forbidden_count": 0,
                                                     "isolation_return_code": isolation.metrics.get("return_code")})
