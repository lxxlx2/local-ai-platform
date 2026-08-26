from __future__ import annotations

from enum import StrEnum
import os
from pathlib import Path
import subprocess

from .supervisor_contracts import CandidateIdentityProvider, RepoAccessPolicy


class GenericRepoAccessPolicy(RepoAccessPolicy):
    """Allow one explicitly authorized repository while preserving file-level denies."""

    def __post_init__(self):
        root = Path(self.repo_root).resolve()
        object.__setattr__(self, "repo_root", root)
        if not self.default_allowed_paths:
            object.__setattr__(self, "default_allowed_paths", (root,))

    def validate_allowed_paths(self, paths):
        if not paths:
            raise PermissionError("at least one bounded allowed path is required")
        root = self.repo_root.resolve()
        defaults = tuple(Path(path).resolve() for path in self.default_allowed_paths)
        validated = []
        for path in paths:
            raw = Path(path)
            resolved = raw.resolve(strict=True)
            if resolved == root:
                if root not in defaults:
                    raise PermissionError("repository root is not explicitly authorized")
            else:
                resolved, _relative = self._relative(raw)
                if not any(resolved == default or resolved.is_relative_to(default) for default in defaults):
                    raise PermissionError("allowed path is outside approved project roots")
            validated.append(resolved)
        return tuple(dict.fromkeys(validated))


class GenericRepoWritePolicy:
    """Text/code-only write policy for an explicitly authorized project worktree."""

    allowed_suffixes = frozenset({
        ".py", ".pyi", ".md", ".json", ".toml", ".yaml", ".yml", ".txt",
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".kts",
        ".go", ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".html", ".css",
        ".scss", ".sql", ".sh", ".bash", ".zsh", ".xml", ".gradle", ".properties",
    })
    allowed_names = frozenset({
        "Dockerfile", "Makefile", "Procfile", ".gitignore", ".dockerignore", ".npmrc.example",
    })

    def __init__(self, repo_root: Path, write_roots=()):
        self.repo_root = Path(repo_root).resolve()
        self.write_roots = tuple(Path(path).resolve() for path in write_roots) or (self.repo_root,)
        for root in self.write_roots:
            if root != self.repo_root and not root.is_relative_to(self.repo_root):
                raise PermissionError("write root escapes project repository")

    def validate_write_path(self, value: Path | str) -> Path:
        policy = GenericRepoAccessPolicy(self.repo_root)
        candidate, _ = policy._relative(Path(value), allow_missing=True)
        if not any(candidate == root or candidate.is_relative_to(root) for root in self.write_roots):
            raise PermissionError("write path outside bounded project write roots")
        if candidate.name not in self.allowed_names and candidate.suffix.lower() not in self.allowed_suffixes:
            raise PermissionError("generic project write file type denied")
        return candidate

    def validate_git_ownership(self, value: Path | str) -> Path:
        candidate = self.validate_write_path(value)
        relative = candidate.relative_to(self.repo_root).as_posix()
        environment = {
            "PATH": os.defpath,
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        tracked = subprocess.run(
            ("git", "ls-files", "--error-unmatch", "--", relative),
            cwd=self.repo_root,
            capture_output=True,
            shell=False,
            timeout=10,
            check=False,
            env=environment,
        )
        if tracked.returncode == 0:
            return candidate
        ignored = subprocess.run(
            ("git", "check-ignore", "--no-index", "-q", "--", relative),
            cwd=self.repo_root,
            capture_output=True,
            shell=False,
            timeout=10,
            check=False,
            env=environment,
        )
        if ignored.returncode == 0:
            raise PermissionError("IGNORED_WRITE_TARGET_DENIED")
        if ignored.returncode != 1:
            raise PermissionError("GIT_OWNERSHIP_CHECK_FAILED")
        return candidate


class GenericCandidateIdentityProvider(CandidateIdentityProvider):
    def __init__(self, repo_root: Path):
        super().__init__(repo_root, policy=GenericRepoAccessPolicy(Path(repo_root)))

    def unowned_write_root_paths(self, write_roots=()):
        write_policy = GenericRepoWritePolicy(self.repo_root, write_roots)
        relative_roots = [
            "." if root == self.repo_root else root.relative_to(self.repo_root).as_posix()
            for root in write_policy.write_roots
        ]
        payload = self._git(
            "ls-files", "--others", "--ignored", "--exclude-standard", "-z",
            "--", *relative_roots,
        )
        paths = []
        for value in payload.decode("utf-8", errors="strict").split("\0"):
            if not value:
                continue
            try:
                write_policy.validate_write_path(self.repo_root / value)
            except PermissionError:
                continue
            paths.append(value)
        return tuple(sorted(set(paths)))


class TestProfile(StrEnum):
    NONE = "none"
    PYTEST = "pytest"
    NPM_TEST = "npm-test"
    GO_TEST = "go-test"
    CARGO_TEST = "cargo-test"


TEST_PROFILE_ARGV = {
    TestProfile.NONE: (),
    TestProfile.PYTEST: ("python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
    TestProfile.NPM_TEST: ("npm", "test", "--", "--runInBand"),
    TestProfile.GO_TEST: ("go", "test", "./..."),
    TestProfile.CARGO_TEST: ("cargo", "test"),
}
