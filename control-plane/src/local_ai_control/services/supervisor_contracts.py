from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol

from local_ai_control.services.security import SecretFirewall

AI_ROOT = Path("/Users/jerson/AI")
CONTROL_PLANE_ROOT = AI_ROOT / "control-plane"
CONTROL_PLANE_PYTHON = AI_ROOT / "runtime/control-plane-venv/bin/python"
SUPERVISOR_RUNTIME = AI_ROOT / "runtime/supervisor"
SUPERVISOR_DB = SUPERVISOR_RUNTIME / "supervisor.db"
MAX_ACTIVE_JOBS = 1
MAX_SUMMARY_CHARS = 4096
MAX_EVENT_PAYLOAD_CHARS = 2048
MAX_EVENTS_PER_JOB = 5000
MAX_TERMINAL_JOBS = 500
MAX_FINDINGS_PER_REVIEW = 100
MAX_FINDINGS_PER_JOB = 500
MAX_WORK_UNIT_PROMPT_BYTES = 256_000
MAX_CONTENT_FILES = 2_000
LOCK_TTL_SECONDS = 30
MAX_CANDIDATE_IDENTITY_FILES = 2_000
MAX_CANDIDATE_IDENTITY_BYTES = 16_000_000
MAX_SAFE_AGENT_FILE_BYTES = 1_000_000
MAX_MUTATING_JOBS_IN_SYSTEM = 1
MAX_REVIEW_PATCH_BYTES = 256_000
EMPTY_REVIEW_PATCH = "# LOCAL_AI_SUPERVISOR_NO_CANDIDATE_CHANGES\n"


@dataclass(frozen=True)
class RepoAccessPolicy:
    """One path policy for Producer, Revision, Reviewer, and candidate probes."""

    repo_root: Path = AI_ROOT
    default_allowed_paths: tuple[Path, ...] = ()

    denied_parts = frozenset({
        "runtime", "models", "cache", "tmp", "logs", "secrets", "credentials",
        "inbox", "output", "private", "content", ".git",
    })
    denied_suffixes = frozenset({
        ".env", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx",
    })

    def __post_init__(self):
        root = Path(self.repo_root).resolve()
        object.__setattr__(self, "repo_root", root)
        if not self.default_allowed_paths:
            # The download queue is a versioned production input, but the rest
            # of config/ remains outside the agent/reviewer boundary.
            defaults=[root/"control-plane",root/"docs"]
            queue_config=root/"config/model-download-queue-v0.1.json"
            if queue_config.is_file(): defaults.append(queue_config)
            object.__setattr__(self,"default_allowed_paths",tuple(defaults))

    def _relative(self, path: Path, *, allow_missing: bool = False) -> tuple[Path, Path]:
        root = self.repo_root.resolve()
        raw = Path(path)
        if ".." in raw.parts:
            raise PermissionError("repository path traversal denied")
        if not raw.is_absolute():
            raw = root / raw
        resolved = raw.resolve(strict=not allow_missing)
        if resolved == root or not resolved.is_relative_to(root):
            raise PermissionError("repository root blanket/traversal access denied")
        try:
            lexical_relative = raw.relative_to(root)
        except ValueError as error:
            raise PermissionError("repository path traversal denied") from error
        current = root
        for part in lexical_relative.parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError("repository symlink access denied")
        relative = resolved.relative_to(root)
        if any(part.lower() in self.denied_parts for part in relative.parts):
            raise PermissionError("runtime/secret repository path denied")
        lowered = relative.as_posix().lower()
        if any(lowered.endswith(suffix) for suffix in self.denied_suffixes) or Path(lowered).name.startswith(".env"):
            raise PermissionError("credential/database repository path denied")
        if raw.is_symlink():
            raise PermissionError("repository symlink access denied")
        return resolved, relative

    def validate_allowed_paths(self, paths: tuple[Path, ...] | list[Path]) -> tuple[Path, ...]:
        if not paths:
            raise PermissionError("at least one bounded allowed path is required")
        validated = []
        defaults = tuple(path.resolve() for path in self.default_allowed_paths)
        for path in paths:
            resolved, _ = self._relative(Path(path))
            if not any(resolved == default or resolved.is_relative_to(default) for default in defaults):
                raise PermissionError("allowed path is outside approved source/document roots")
            validated.append(resolved)
        return tuple(dict.fromkeys(validated))

    def validate_candidate_path(self, value: str, allowed_paths: tuple[Path, ...], *, deleted: bool = False) -> str:
        if not value or Path(value).is_absolute() or ".." in Path(value).parts:
            raise PermissionError("candidate path traversal denied")
        resolved, relative = self._relative(Path(value), allow_missing=True)
        allowed = self.validate_allowed_paths(list(allowed_paths))
        if not any(resolved == root or resolved.is_relative_to(root) for root in allowed):
            raise PermissionError("candidate path outside reviewer allowed paths")
        return relative.as_posix()

    def _safe_manifest_entry(self, value: str, allowed: tuple[Path, ...], *, scan_secrets: bool = False) -> dict:
        normalized = self.validate_candidate_path(value, allowed)
        candidate = self.repo_root / normalized
        if candidate.is_symlink() or not candidate.is_file():
            raise PermissionError("safe manifest requires a regular non-symlink file")
        payload = candidate.read_bytes()
        if len(payload) > MAX_SAFE_AGENT_FILE_BYTES:
            raise ValueError("safe manifest file exceeds scan bound")
        if b"\0" in payload:
            raise ValueError("safe manifest binary content denied")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("safe manifest non-UTF-8 content denied") from error
        if scan_secrets and SecretFirewall().inspect(text).action == "BLOCK":
            raise ValueError("safe manifest content rejected by Secret Firewall")
        return {"path": normalized, "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}

    def build_safe_file_manifest(self, allowed_paths: tuple[Path, ...] | list[Path]) -> tuple[dict, ...]:
        """Return a bounded manifest of safe tracked files; parent access is never delegated."""
        allowed = self.validate_allowed_paths(list(allowed_paths))
        relative_roots = [path.relative_to(self.repo_root).as_posix() for path in allowed]
        completed = subprocess.run(
            ("git", "ls-files", "-z", "--", *relative_roots), cwd=self.repo_root,
            capture_output=True, shell=False, timeout=10, check=False,
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"},
        )
        if completed.returncode != 0:
            raise RuntimeError("safe tracked-file manifest Git probe failed")
        paths = sorted({item for item in completed.stdout.decode("utf-8", errors="strict").split("\0") if item})
        if len(paths) > MAX_CANDIDATE_IDENTITY_FILES:
            raise ValueError("safe tracked-file manifest exceeds file bound")
        manifest, total = [], 0
        for value in paths:
            try:
                normalized = self.validate_candidate_path(value, allowed)
            except PermissionError:
                continue
            entry = self._safe_manifest_entry(normalized, allowed, scan_secrets=True)
            total += int(entry["size_bytes"])
            if total > MAX_CANDIDATE_IDENTITY_BYTES:
                raise ValueError("safe tracked-file manifest exceeds content bound")
            manifest.append(entry)
        return tuple(manifest)

    def build_candidate_file_manifest(self, identity: "CandidateIdentity",
                                      allowed_paths: tuple[Path, ...] | list[Path]) -> tuple[dict, ...]:
        """Manifest every non-deleted immutable-candidate path, including safe untracked files."""
        allowed = self.validate_allowed_paths(list(allowed_paths))
        deleted = set(identity.deleted_paths)
        values = [path for path in identity.candidate_paths if path not in deleted]
        if len(values) > MAX_CANDIDATE_IDENTITY_FILES:
            raise ValueError("candidate safe manifest exceeds file bound")
        manifest, total = [], 0
        for value in sorted(set(values)):
            entry = self._safe_manifest_entry(value, allowed, scan_secrets=True)
            total += int(entry["size_bytes"])
            if total > MAX_CANDIDATE_IDENTITY_BYTES:
                raise ValueError("candidate safe manifest exceeds content bound")
            manifest.append(entry)
        return tuple(manifest)

    def merge_candidate_manifest(self, identity: "CandidateIdentity",
                                 allowed_paths: tuple[Path, ...] | list[Path]) -> tuple[dict, ...]:
        tracked = self.build_safe_file_manifest(allowed_paths)
        candidate = self.build_candidate_file_manifest(identity, allowed_paths)
        return tuple(sorted({item["path"]: item for item in (*tracked, *candidate)}.values(),
                            key=lambda item: item["path"]))

    def validate_supplied_manifest(self, manifest: tuple[Mapping, ...] | list[Mapping],
                                   allowed_paths: tuple[Path, ...] | list[Path],
                                   candidate_identity: "CandidateIdentity | None" = None) -> tuple[dict, ...]:
        allowed = self.validate_allowed_paths(list(allowed_paths))
        if len(manifest) > MAX_CANDIDATE_IDENTITY_FILES:
            raise ValueError("safe manifest exceeds file bound")
        checked, total, seen = [], 0, set()
        for raw in manifest:
            path = str(raw.get("path", ""))
            if path in seen:
                raise ValueError("safe manifest contains duplicate path")
            seen.add(path)
            entry = self._safe_manifest_entry(path, allowed, scan_secrets=True)
            if entry != {"path": path, "sha256": str(raw.get("sha256", "")),
                         "size_bytes": int(raw.get("size_bytes", -1))}:
                raise ValueError("safe manifest is stale or invalid")
            total += int(entry["size_bytes"])
            if total > MAX_CANDIDATE_IDENTITY_BYTES:
                raise ValueError("safe manifest exceeds content bound")
            checked.append(entry)
        tracked = {item["path"]: item for item in self.build_safe_file_manifest(allowed)}
        supplied = {item["path"]: item for item in checked}
        if any(supplied.get(path) != item for path, item in tracked.items()):
            raise ValueError("safe manifest omits or changes tracked content")
        extras = set(supplied) - set(tracked)
        if extras:
            if candidate_identity is None:
                raise PermissionError("safe manifest contains unbound extra path")
            permitted = set(candidate_identity.candidate_paths) - set(candidate_identity.deleted_paths)
            if not extras <= permitted:
                raise PermissionError("safe manifest extra path is outside candidate identity")
        return tuple(checked)

    def read_safe_file(self, value: str, allowed_paths: tuple[Path, ...] | list[Path],
                       manifest: tuple[Mapping, ...] | list[Mapping]) -> bytes:
        normalized = self.validate_candidate_path(value, tuple(allowed_paths))
        entries = {str(item.get("path", "")): item for item in manifest}
        entry = entries.get(normalized)
        if entry is None:
            raise PermissionError("file is outside the safe tracked-file manifest")
        candidate = self.repo_root / normalized
        if candidate.is_symlink() or not candidate.is_file():
            raise PermissionError("safe manifest file is no longer a regular file")
        payload = candidate.read_bytes()
        if (len(payload) != int(entry.get("size_bytes", -1))
                or hashlib.sha256(payload).hexdigest() != str(entry.get("sha256", ""))):
            raise ValueError("safe tracked-file manifest integrity mismatch")
        return payload


AgentPathPolicy = RepoAccessPolicy


@dataclass(frozen=True)
class RepoWritePolicy:
    """Path-level mutation policy; it is separate from immutable read manifests."""

    repo_root: Path = AI_ROOT
    write_roots: tuple[Path, ...] = ()
    allowed_suffixes = frozenset({".py", ".md", ".json", ".toml", ".yaml", ".yml", ".txt"})

    def __post_init__(self):
        root = Path(self.repo_root).resolve()
        object.__setattr__(self, "repo_root", root)
        if not self.write_roots:
            object.__setattr__(self, "write_roots", (
                root / "control-plane/src", root / "control-plane/tests", root / "docs",
            ))

    def validate_write_path(self, value: Path | str) -> Path:
        policy = RepoAccessPolicy(self.repo_root)
        candidate, _ = policy._relative(Path(value), allow_missing=True)
        roots = tuple(Path(item).resolve() for item in self.write_roots)
        approved = (
            self.repo_root / "control-plane/src", self.repo_root / "control-plane/tests", self.repo_root / "docs",
        )
        if not all(any(root == base or root.is_relative_to(base) for base in approved) for root in roots):
            raise PermissionError("write root outside approved source/test/document roots")
        if not any(candidate == root or candidate.is_relative_to(root) for root in roots):
            raise PermissionError("write path outside bounded write roots")
        if candidate.suffix.lower() not in self.allowed_suffixes:
            raise PermissionError("write file type denied")
        return candidate

    def validate_git_ownership(self, value: Path | str) -> Path:
        """Allow tracked targets and non-ignored new targets; deny ignored files."""
        candidate = self.validate_write_path(value)
        relative = candidate.relative_to(self.repo_root).as_posix()
        environment = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"}
        tracked = subprocess.run(
            ("git", "ls-files", "--error-unmatch", "--", relative), cwd=self.repo_root,
            capture_output=True, shell=False, timeout=10, check=False, env=environment,
        )
        if tracked.returncode == 0:
            return candidate
        ignored = subprocess.run(
            ("git", "check-ignore", "--no-index", "-q", "--", relative), cwd=self.repo_root,
            capture_output=True, shell=False, timeout=10, check=False, env=environment,
        )
        if ignored.returncode == 0:
            raise PermissionError("IGNORED_WRITE_TARGET_DENIED")
        if ignored.returncode != 1:
            raise PermissionError("GIT_OWNERSHIP_CHECK_FAILED")
        return candidate


@dataclass(frozen=True)
class CandidateIdentity:
    candidate_ref_type: str
    candidate_commit_sha: str | None
    candidate_tree_sha: str | None
    base_commit_sha: str
    candidate_diff_sha256: str
    candidate_created_at: str
    candidate_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    renamed_paths: tuple[tuple[str, str], ...] = ()
    copied_paths: tuple[tuple[str, str], ...] = ()

    def stable_payload(self) -> dict:
        return {
            "candidate_ref_type": self.candidate_ref_type,
            "candidate_commit_sha": self.candidate_commit_sha,
            "candidate_tree_sha": self.candidate_tree_sha,
            "base_commit_sha": self.base_commit_sha,
            "candidate_diff_sha256": self.candidate_diff_sha256,
            "candidate_paths": list(self.candidate_paths),
            "deleted_paths": list(self.deleted_paths),
            "renamed_paths": [list(item) for item in self.renamed_paths],
            "copied_paths": [list(item) for item in self.copied_paths],
        }

    def to_mapping(self) -> dict:
        return self.stable_payload() | {"candidate_created_at": self.candidate_created_at}

    @classmethod
    def from_mapping(cls, value: Mapping) -> "CandidateIdentity":
        ref_type = str(value.get("candidate_ref_type", ""))
        commit = value.get("candidate_commit_sha")
        tree = value.get("candidate_tree_sha")
        base = str(value.get("base_commit_sha", ""))
        diff = str(value.get("candidate_diff_sha256", ""))
        created = str(value.get("candidate_created_at", ""))
        paths = tuple(str(item) for item in value.get("candidate_paths", ()))
        deleted = tuple(str(item) for item in value.get("deleted_paths", ()))
        renamed = tuple(tuple(str(part) for part in item) for item in value.get("renamed_paths", ()))
        copied = tuple(tuple(str(part) for part in item) for item in value.get("copied_paths", ()))
        if any(len(item) != 2 for item in (*renamed, *copied)):
            raise ValueError("invalid candidate rename/copy provenance")
        path_set, deleted_set = set(paths), set(deleted)
        if len(path_set) != len(paths) or len(set(renamed)) != len(renamed) or len(set(copied)) != len(copied):
            raise ValueError("duplicate candidate path provenance")
        if any(old not in path_set or new not in path_set or old not in deleted_set or old == new
               for old, new in renamed):
            raise ValueError("rename provenance is not bound to candidate paths")
        if any(old not in path_set or new not in path_set or old == new for old, new in copied):
            raise ValueError("copy provenance is not bound to candidate paths")
        if ref_type not in {"COMMIT", "TREE_MANIFEST"}:
            raise ValueError("invalid candidate identity reference type")
        for digest in (base, diff, commit or "0" * 40, tree or "0" * 40):
            if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", digest):
                raise ValueError("invalid candidate identity digest")
        if ref_type == "COMMIT" and not commit:
            raise ValueError("commit candidate identity requires commit SHA")
        if ref_type == "TREE_MANIFEST" and not tree:
            raise ValueError("tree candidate identity requires tree SHA")
        try:
            created_value = datetime.fromisoformat(created)
        except ValueError as error:
            raise ValueError("invalid candidate identity creation timestamp") from error
        if created_value.tzinfo is None:
            raise ValueError("candidate identity creation timestamp must be timezone-aware")
        return cls(ref_type, str(commit) if commit else None, str(tree) if tree else None,
                   base, diff, created, paths, deleted, renamed, copied)

    def same_candidate(self, other: "CandidateIdentity") -> bool:
        return self.stable_payload() == other.stable_payload()


class CandidateIdentityProvider:
    """Read-only, bounded, deterministic Git/worktree candidate identity probe."""

    def __init__(self, repo_root: Path = AI_ROOT, policy: RepoAccessPolicy | None = None,
                 timeout_seconds: float = 10):
        self.repo_root = Path(repo_root).resolve()
        self.policy = policy or RepoAccessPolicy(self.repo_root)
        self.timeout_seconds = min(max(float(timeout_seconds), 1), 30)

    def _git(self, *args: str) -> bytes:
        completed = subprocess.run(
            ("git", *args), cwd=self.repo_root, capture_output=True, shell=False,
            timeout=self.timeout_seconds, check=False,
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"},
        )
        if completed.returncode != 0:
            raise RuntimeError("candidate identity Git probe failed")
        return completed.stdout

    def validate_baseline(self, baseline_commit_sha: str) -> str:
        value = str(baseline_commit_sha)
        if not re.fullmatch(r"[a-f0-9]{40}", value):
            raise ValueError("trusted baseline requires a full commit SHA")
        if self._git("cat-file", "-t", value).decode().strip() != "commit":
            raise ValueError("trusted baseline does not identify a commit")
        self._git("merge-base", "--is-ancestor", value, "HEAD")
        return value

    def capture_baseline(self) -> str:
        return self.validate_baseline(self._git("rev-parse", "HEAD").decode().strip())

    def worktree_is_clean(self) -> bool:
        return not bool(self._git("status", "--porcelain=v1", "-z", "--untracked-files=all"))

    def unowned_write_root_paths(self, write_roots: tuple[Path, ...] = ()) -> tuple[str, ...]:
        """Return ignored pre-existing files that the bounded producer policy could mutate."""
        write_policy = RepoWritePolicy(self.repo_root, write_roots)
        relative_roots = [Path(root).resolve().relative_to(self.repo_root).as_posix()
                          for root in write_policy.write_roots]
        payload = self._git("ls-files", "--others", "--ignored", "--exclude-standard", "-z",
                            "--", *relative_roots)
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

    def build_review_patch(self, identity: "CandidateIdentity") -> str:
        """Build a bounded, text-only patch for the exact immutable candidate."""
        if not identity.same_candidate(self.snapshot(identity.base_commit_sha)):
            raise ValueError("review patch candidate is stale")
        allowed = self.policy.validate_allowed_paths(list(self.policy.default_allowed_paths))
        paths = tuple(sorted(set(identity.candidate_paths)))
        if not paths:
            return EMPTY_REVIEW_PATCH
        for value in paths:
            self.policy.validate_candidate_path(value, allowed, deleted=value in identity.deleted_paths)
            if value not in identity.deleted_paths:
                self.policy._safe_manifest_entry(value, allowed, scan_secrets=True)
        tracked = set(self._git("ls-files", "-z", "--", *paths).decode("utf-8", errors="strict").split("\0"))
        tracked.discard("")
        patch = self._git("diff", "--no-ext-diff", "--no-color", "--unified=3",
                          identity.base_commit_sha, "--", *paths)
        if b"\0" in patch:
            raise ValueError("review patch binary content denied")
        text = patch.decode("utf-8", errors="strict")
        if "GIT binary patch" in text or re.search(r"^Binary files .* differ$", text, re.MULTILINE):
            raise ValueError("review patch binary content denied")
        for value in paths:
            if value in tracked or value in identity.deleted_paths:
                continue
            entry = self.policy._safe_manifest_entry(value, allowed, scan_secrets=True)
            payload = (self.repo_root / value).read_text(encoding="utf-8")
            lines = payload.splitlines(keepends=True)
            text += (f"diff --git a/{value} b/{value}\nnew file mode 100644\n"
                     f"--- /dev/null\n+++ b/{value}\n@@ -0,0 +1,{len(lines)} @@\n")
            text += "".join("+" + line for line in lines)
            if payload and not payload.endswith("\n"):
                text += "\n\\ No newline at end of file\n"
            if int(entry["size_bytes"]) > MAX_SAFE_AGENT_FILE_BYTES:
                raise ValueError("review patch file exceeds bound")
        encoded = text.encode("utf-8")
        if not encoded or len(encoded) > MAX_REVIEW_PATCH_BYTES:
            raise ValueError("review patch outside safe size bound")
        if SecretFirewall().inspect(text).action == "BLOCK":
            raise ValueError("review patch rejected by Secret Firewall")
        return text

    @staticmethod
    def _parse_name_status(payload: bytes) -> list[tuple[str, str, str | None]]:
        fields = payload.decode("utf-8", errors="strict").split("\0")
        result, index = [], 0
        while index < len(fields) and fields[index]:
            status = fields[index]; index += 1
            if index >= len(fields):
                raise ValueError("invalid Git name-status payload")
            path = fields[index]; index += 1
            source = None
            if status.startswith(("R", "C")):
                if index >= len(fields):
                    raise ValueError("invalid Git rename payload")
                source = path
                path = fields[index]; index += 1
            result.append((status[:1], path, source))
        return result

    def snapshot(self, base_commit_sha: str | None = None) -> CandidateIdentity:
        head = self._git("rev-parse", "HEAD").decode().strip()
        base = self.validate_baseline(base_commit_sha) if base_commit_sha else self.validate_baseline(
            self._git("rev-parse", "main").decode().strip()
        )
        if not re.fullmatch(r"[a-f0-9]{40}", head) or not re.fullmatch(r"[a-f0-9]{40}", base):
            raise ValueError("candidate identity requires full Git SHAs")
        changes = self._parse_name_status(self._git(
            "diff", "--name-status", "-z", "--find-renames", "--find-copies-harder", base, "--",
        ))
        untracked = [value for value in self._git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0") if value]
        # Git does not include unstaged, untracked rename destinations in `git diff`.
        # Recover an unambiguous exact-content move so the trusted old path is not lost.
        for deleted_entry in tuple(item for item in changes if item[0] == "D"):
            source = deleted_entry[1]
            self.policy.validate_candidate_path(source, self.policy.default_allowed_paths, deleted=True)
            baseline_size = int(self._git("cat-file", "-s", f"{base}:{source}").decode().strip())
            if baseline_size > MAX_CANDIDATE_IDENTITY_BYTES:
                continue
            baseline_payload = self._git("show", f"{base}:{source}")
            matches = []
            for path in untracked:
                try:
                    normalized = self.policy.validate_candidate_path(
                        path, self.policy.default_allowed_paths, deleted=False,
                    )
                except PermissionError:
                    continue
                candidate = self.repo_root / normalized
                if (candidate.is_file() and not candidate.is_symlink()
                        and candidate.stat().st_size <= MAX_CANDIDATE_IDENTITY_BYTES
                        and candidate.read_bytes() == baseline_payload):
                    matches.append(path)
            if len(matches) == 1:
                changes.remove(deleted_entry)
                changes.append(("R", matches[0], source))
                untracked.remove(matches[0])
        by_path: dict[str, str] = {}
        renamed: list[tuple[str, str]] = []
        copied: list[tuple[str, str]] = []
        for status, path, source in changes:
            by_path[path] = status
            if source is not None:
                by_path[source] = "D" if status == "R" else "C"
                (renamed if status == "R" else copied).append((source, path))
        by_path.update({path: "A" for path in untracked})
        if len(by_path) > MAX_CANDIDATE_IDENTITY_FILES:
            raise ValueError("candidate identity file count exceeds bound")
        manifest, total = [], 0
        deleted = []
        allowed = self.policy.default_allowed_paths
        for path, status in sorted(by_path.items()):
            is_deleted = status == "D"
            normalized = self.policy.validate_candidate_path(path, allowed, deleted=is_deleted)
            if is_deleted:
                deleted.append(normalized)
                manifest.append({"path": normalized, "status": "D", "sha256": None})
                continue
            candidate = (self.repo_root / normalized)
            if candidate.is_symlink() or not candidate.is_file():
                raise PermissionError("candidate identity refuses symlink/non-file")
            data = candidate.read_bytes()
            total += len(data)
            if total > MAX_CANDIDATE_IDENTITY_BYTES:
                raise ValueError("candidate identity content exceeds bound")
            manifest.append({"path": normalized, "status": status, "sha256": hashlib.sha256(data).hexdigest()})
        encoded = _json_exact({"base": base, "head": head, "manifest": manifest}, 1_000_000)
        dirty = bool(self._git("status", "--porcelain=v1", "-z", "--untracked-files=all"))
        head_tree = self._git("rev-parse", "HEAD^{tree}").decode().strip()
        tree_sha = (hashlib.sha256(_json_exact({"head_tree": head_tree, "manifest": manifest}, 1_000_000).encode()).hexdigest()
                    if dirty else head_tree)
        diff_sha = hashlib.sha256(encoded.encode()).hexdigest()
        return CandidateIdentity(
            "TREE_MANIFEST" if dirty else "COMMIT", None if dirty else head, tree_sha,
            base, diff_sha, utc_now(),
            tuple(item["path"] for item in manifest), tuple(deleted),
            tuple(sorted(renamed)), tuple(sorted(copied)),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_private_directory(path: Path) -> Path:
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(0o700)
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError as error:
        raise PermissionError(f"unable to enforce owner-only directory permissions: {target}") from error
    if mode & 0o077:
        raise PermissionError(f"owner-only directory permission policy failed: {target}")
    return target


def ensure_private_file(path: Path) -> Path:
    target = Path(path)
    if not target.exists():
        return target
    try:
        target.chmod(0o600)
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError as error:
        raise PermissionError(f"unable to enforce owner-only file permissions: {target}") from error
    if mode & 0o077:
        raise PermissionError(f"owner-only file permission policy failed: {target}")
    return target


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"


class WorkflowStage(str, Enum):
    INTAKE = "INTAKE"
    PRODUCER = "PRODUCER"
    VALIDATION = "VALIDATION"
    SELF_ACCEPTANCE = "SELF_ACCEPTANCE"
    REVIEW = "REVIEW"
    REVISION = "REVISION"
    SECURITY = "SECURITY"
    GIT_GATE = "GIT_GATE"
    DONE = "DONE"


class StageResultStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WAIT = "WAIT"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"


class LeaseLostError(RuntimeError):
    """Raised when the process no longer owns the singleton lease."""


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    file: str | None
    evidence: str
    recommended_fix: str
    scope: str = "FILE"


@dataclass(frozen=True)
class PersistedReviewFinding:
    finding_id: str
    job_id: str
    review_round: int
    severity: str
    file: str | None
    evidence: str
    recommended_fix: str
    created_at: str
    integrity_hash: str
    status: str
    consumed_by_revision: str | None
    scope: str = "FILE"


@dataclass(frozen=True)
class WorkflowJob:
    job_id: str
    title: str
    project_scope: str
    created_at: str
    updated_at: str
    owner_id: str
    risk_level: str
    status: JobStatus
    current_stage: WorkflowStage
    attempt: int
    review_round: int
    max_review_rounds: int
    max_attempts_per_stage: int
    last_error: str | None
    resume_state: str | None
    created_by: str
    metadata: dict
    next_retry_at: float | None
    baseline_commit_sha: str | None = None
    mutation_capable: bool = True
    baseline_candidate_state_sha256: str | None = None


@dataclass(frozen=True)
class StageContext:
    job: WorkflowJob
    stage: WorkflowStage
    attempt: int
    idempotency_key: str
    timeout_seconds: float
    repository: "SupervisorRepository"

    def current_review_findings(self) -> tuple[PersistedReviewFinding, ...]:
        if self.job.review_round <= 0:
            return ()
        return tuple(self.repository.review_findings(self.job.job_id, self.job.owner_id, self.job.review_round))


@dataclass(frozen=True)
class StageResult:
    status: StageResultStatus
    summary: str
    artifacts: tuple[dict, ...] = ()
    error: str | None = None
    metrics: dict = field(default_factory=dict)
    next_hint: str | None = None
    review_findings: tuple[ReviewFinding, ...] = ()

    @classmethod
    def passed(cls, summary: str, **kwargs) -> "StageResult":
        return cls(StageResultStatus.PASS, summary, **kwargs)

    @classmethod
    def failed(cls, summary: str, error: str | None = None, **kwargs) -> "StageResult":
        return cls(StageResultStatus.FAIL, summary, error=error, **kwargs)


class StageRunner(Protocol):
    def run(self, context: StageContext) -> StageResult: ...


def _bounded(value: str | None, limit: int = MAX_SUMMARY_CHARS) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: max(0, limit - 15)] + "…[TRUNCATED]"


def _json_exact(value, limit: int) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    if len(encoded.encode()) > limit:
        raise ValueError("structured payload exceeds safe persistence bound")
    return encoded


def _safe_json(value, limit=MAX_EVENT_PAYLOAD_CHARS) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) > limit:
        encoded = json.dumps({"truncated": True, "sha256": hashlib.sha256(encoded.encode()).hexdigest()})
    return encoded


def _safe_text(value: str | None, limit: int = MAX_SUMMARY_CHARS) -> str | None:
    bounded = _bounded(value, limit)
    if bounded and SecretFirewall().inspect(bounded).action == "BLOCK":
        return "[REDACTED_BY_SECRET_FIREWALL]"
    return bounded


def _safe_audit_value(value):
    if isinstance(value, Mapping):
        return {str(key): _safe_audit_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_audit_value(item) for item in value]
    if isinstance(value, str) and SecretFirewall().inspect(value).action == "BLOCK":
        return {"redacted": True, "sha256": hashlib.sha256(value.encode()).hexdigest()}
    return value


def _safe_metadata(metadata: Mapping | None) -> dict:
    def sanitize(value):
        if isinstance(value, Mapping):
            clean = {}
            for key, item in value.items():
                name = str(key)
                if (re.search(r"prompt|token|secret|password|credential|cookie|authorization", name, re.I)
                        and not name.endswith("_sha256")):
                    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
                    clean[f"{name}_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
                else:
                    clean[name] = sanitize(item)
            return clean
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        return _safe_audit_value(value)

    clean = sanitize(dict(metadata or {}))
    return json.loads(_safe_json(clean, 16_000))


def _normalize_relative_path(value: str, root: Path, label: str) -> str:
    if not value:
        return ""
    raw = Path(value)
    candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise PermissionError(f"{label} path traversal denied")
    return candidate.relative_to(root.resolve()).as_posix()


def _safe_review_text(value: str) -> str:
    if SecretFirewall().inspect(value).action == "BLOCK":
        return "[REDACTED_BY_SECRET_FIREWALL]"
    return _bounded(value, MAX_SUMMARY_CHARS) or ""


@dataclass(frozen=True)
class ReviewResult:
    status: str
    findings: tuple[ReviewFinding, ...] = ()

    def to_stage_result(self, repo_root: Path = AI_ROOT) -> StageResult:
        if self.status not in {"PASS", "FAIL"}:
            raise ValueError("review status must be PASS or FAIL")
        if self.status == "PASS" and self.findings:
            raise ValueError("PASS review cannot contain findings")
        normalized = []
        root = Path(repo_root).resolve()
        for finding in self.findings:
            if finding.severity not in {"BLOCKING", "HIGH", "MEDIUM", "LOW"}:
                raise ValueError("invalid review severity")
            scope = finding.scope or "FILE"
            if scope == "FILE":
                if not finding.file:
                    raise ValueError("FILE review finding requires a file")
                path = _normalize_relative_path(finding.file, root, "review finding")
            elif scope == "WORKFLOW":
                if finding.file:
                    raise ValueError("WORKFLOW review finding cannot reference a path")
                path = None
            else:
                raise ValueError("invalid review finding scope")
            normalized.append({
                "scope": scope,
                "severity": finding.severity,
                "file": path,
                "evidence_sha256": hashlib.sha256(finding.evidence.encode()).hexdigest(),
                "recommended_fix_sha256": hashlib.sha256(finding.recommended_fix.encode()).hexdigest(),
            })
        digest = hashlib.sha256(_json_exact(normalized, 64_000).encode()).hexdigest()
        metrics = {
            "findings_count": len(normalized),
            "blocking_findings": sum(item["severity"] == "BLOCKING" for item in normalized),
        }
        artifact = ({"kind": "review_metadata", "reference": f"review:{digest}", "size_bytes": 0},)
        if self.status == "PASS":
            return StageResult.passed("Independent review contract returned PASS", metrics=metrics, artifacts=artifact)
        return StageResult.failed(
            "Independent review contract returned FAIL",
            metrics=metrics,
            artifacts=artifact,
            review_findings=self.findings,
        )


TERMINAL_JOB_STATUSES = {JobStatus.FAILED, JobStatus.CANCELED, JobStatus.COMPLETED}
SAFE_RECOVERY_STAGES = {
    WorkflowStage.INTAKE,
    WorkflowStage.VALIDATION,
    WorkflowStage.SELF_ACCEPTANCE,
    WorkflowStage.REVIEW,
    WorkflowStage.SECURITY,
}
NEXT_STAGE = {
    WorkflowStage.INTAKE: WorkflowStage.PRODUCER,
    WorkflowStage.PRODUCER: WorkflowStage.VALIDATION,
    WorkflowStage.VALIDATION: WorkflowStage.SELF_ACCEPTANCE,
    WorkflowStage.SELF_ACCEPTANCE: WorkflowStage.REVIEW,
    WorkflowStage.REVISION: WorkflowStage.VALIDATION,
    WorkflowStage.REVIEW: WorkflowStage.SECURITY,
    WorkflowStage.SECURITY: WorkflowStage.GIT_GATE,
    WorkflowStage.GIT_GATE: WorkflowStage.DONE,
}


@dataclass(frozen=True)
class CodexTaskSpec:
    repo_root: Path
    allowed_paths: tuple[Path, ...]
    task_prompt: str
    risk_level: str
    timeout_seconds: float
    model_role: str
    expected_output_schema: dict
    safe_file_manifest: tuple[dict, ...] = ()
    candidate_identity: CandidateIdentity | None = None
    write_roots: tuple[Path, ...] = ()

    def validate(self) -> dict:
        root = self.repo_root.resolve()
        if root != AI_ROOT.resolve():
            raise PermissionError("Codex repo_root denied")
        policy = RepoAccessPolicy(root)
        allowed = [str(path) for path in policy.validate_allowed_paths(list(self.allowed_paths))]
        generated_manifest = policy.build_safe_file_manifest(tuple(Path(path) for path in allowed))
        manifest = (policy.validate_supplied_manifest(self.safe_file_manifest,
                    tuple(Path(path) for path in allowed), self.candidate_identity)
                    if self.safe_file_manifest else generated_manifest)
        write_policy = RepoWritePolicy(root, self.write_roots)
        write_roots = tuple(str(path.resolve()) for path in write_policy.write_roots)
        for path in write_roots:
            write_policy.validate_write_path(Path(path) / "contract.py")
        if not self.task_prompt or len(self.task_prompt.encode()) > MAX_WORK_UNIT_PROMPT_BYTES:
            raise ValueError("Codex task prompt outside safe size bound")
        if SecretFirewall().inspect(self.task_prompt).action == "BLOCK":
            raise ValueError("Codex task prompt rejected by Secret Firewall")
        if not 1 <= float(self.timeout_seconds) <= 3600:
            raise ValueError("Codex timeout outside safe range")
        schema = _safe_audit_value(self.expected_output_schema)
        _json_exact(schema, 16_000)
        return {
            "repo_root": str(root),
            "allowed_paths": allowed,
            "task_prompt_sha256": hashlib.sha256(self.task_prompt.encode()).hexdigest(),
            "risk_level": self.risk_level,
            "timeout_seconds": float(self.timeout_seconds),
            "model_role": self.model_role,
            "expected_output_schema": schema,
            "safe_file_manifest": list(manifest),
            "candidate_identity": self.candidate_identity.to_mapping() if self.candidate_identity else None,
            "write_roots": list(write_roots),
        }

    def read_safe_file(self, value: str) -> bytes:
        validated = self.validate()
        return RepoAccessPolicy(self.repo_root).read_safe_file(
            value, self.allowed_paths, tuple(validated["safe_file_manifest"]),
        )

    def execution_view(self) -> "CodexTaskSpec":
        validated = self.validate()
        file_paths = tuple(self.repo_root / item["path"] for item in validated["safe_file_manifest"])
        if not file_paths:
            raise PermissionError("safe execution manifest contains no files")
        return CodexTaskSpec(
            self.repo_root, file_paths, self.task_prompt, self.risk_level,
            self.timeout_seconds, self.model_role, self.expected_output_schema,
            tuple(validated["safe_file_manifest"]), self.candidate_identity,
            tuple(Path(path) for path in validated["write_roots"]),
        )

    def validate_write_path(self, value: Path | str) -> Path:
        validated = self.validate()
        return RepoWritePolicy(
            self.repo_root, tuple(Path(path) for path in validated["write_roots"]),
        ).validate_git_ownership(value)


@dataclass(frozen=True)
class WorkUnitSpec:
    work_unit_id: str
    job_id: str
    stage: WorkflowStage
    repo_root: Path
    allowed_paths: tuple[Path, ...]
    risk_level: str
    timeout_seconds: float
    model_role: str
    expected_output_schema: dict
    prompt_content_ref: str
    prompt_sha256: str
    created_at: str
    status: str
    review_round: int
    safe_file_manifest: tuple[dict, ...] = ()
    candidate_identity: CandidateIdentity | None = None
    write_roots: tuple[Path, ...] = ()


class OwnerPrivateContentStore:
    def __init__(self, root: Path):
        self.root = ensure_private_directory(root)

    def _path(self, content_ref: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,96}\.prompt", content_ref):
            raise PermissionError("invalid private content reference")
        candidate = (self.root / content_ref).resolve()
        if candidate.parent != self.root.resolve():
            raise PermissionError("private content path traversal denied")
        return candidate

    def put(self, work_unit_id: str, prompt: str) -> tuple[str, str]:
        encoded = prompt.encode()
        if not encoded or len(encoded) > MAX_WORK_UNIT_PROMPT_BYTES:
            raise ValueError("work unit prompt outside safe size bound")
        if SecretFirewall().inspect(prompt).action == "BLOCK":
            raise ValueError("work unit prompt rejected by Secret Firewall")
        content_ref = f"{work_unit_id}.prompt"
        path = self._path(content_ref)
        digest = hashlib.sha256(encoded).hexdigest()
        if path.exists():
            current = path.read_bytes()
            if hashlib.sha256(current).hexdigest() != digest or current != encoded:
                raise ValueError("work unit content id conflicts with existing content")
            ensure_private_file(path)
            return content_ref, digest
        if sum(1 for _ in self.root.glob("*.prompt")) >= MAX_CONTENT_FILES:
            raise RuntimeError("private content store capacity reached")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            ensure_private_file(path)
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return content_ref, digest

    def get(self, content_ref: str, expected_sha256: str) -> str:
        path = self._path(content_ref)
        ensure_private_file(path)
        data = path.read_bytes()
        if len(data) > MAX_WORK_UNIT_PROMPT_BYTES:
            raise ValueError("private content exceeds safe size bound")
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise ValueError("private content integrity mismatch")
        return data.decode("utf-8")

    def delete(self, content_ref: str) -> None:
        self._path(content_ref).unlink(missing_ok=True)
