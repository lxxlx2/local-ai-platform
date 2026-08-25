from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .codex_qwen_workspace import validate_workspace
from .generic_project_policy import TestProfile
from .supervisor_contracts import ensure_private_directory, ensure_private_file


DEFAULT_GENERIC_PROJECT_RUNTIME = Path("/Users/jerson/AI/runtime/generic-projects")
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")


class GenericProjectError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisteredProject:
    project_id: str
    source_root: str
    source_head_sha: str
    detected_test_profile: str
    registered_at: str


@dataclass(frozen=True)
class TaskWorktree:
    project_id: str
    task_id: str
    source_root: str
    worktree_root: str
    branch: str
    base_commit_sha: str
    test_profile: str
    created_at: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git(root: Path, *args: str, timeout: float = 15) -> str:
    try:
        result = subprocess.run(
            ("/usr/bin/git", "-C", str(root), *args),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
            check=False,
            env={
                "PATH": os.defpath,
                "LANG": "C",
                "LC_ALL": "C",
                "GIT_OPTIONAL_LOCKS": "0",
            },
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GenericProjectError("local Git operation failed") from error
    if result.returncode != 0:
        message = " ".join((result.stderr or "").split())[:300]
        raise GenericProjectError(message or "local Git operation failed")
    return result.stdout.strip()


def _explicit_git_root(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    lexical = raw.absolute()
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise GenericProjectError("project repository does not exist") from error
    if lexical != resolved or raw.is_symlink():
        raise GenericProjectError("symlinked project repository path denied")
    if not resolved.is_dir():
        raise GenericProjectError("project repository must be a directory")
    top = Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != resolved:
        raise GenericProjectError("project path must be the explicit Git worktree root")
    return resolved


def _detect_test_profile(root: Path) -> TestProfile:
    # Detection only examines well-known filenames. No project file contents are
    # executed or interpreted as instructions.
    if (root / "pyproject.toml").is_file() or (root / "pytest.ini").is_file():
        return TestProfile.PYTEST
    if (root / "package.json").is_file():
        return TestProfile.NPM_TEST
    if (root / "go.mod").is_file():
        return TestProfile.GO_TEST
    if (root / "Cargo.toml").is_file():
        return TestProfile.CARGO_TEST
    return TestProfile.NONE


class GenericProjectRegistry:
    """Owner-controlled registry for local Git projects and isolated task worktrees.

    Registration is read-only. Worktree creation performs only the fixed local
    `git worktree add -b` operation. It never fetches, installs dependencies,
    commits, pushes, merges, deploys, or reads instructions from repository files.
    """

    def __init__(self, runtime_root: Path = DEFAULT_GENERIC_PROJECT_RUNTIME):
        self.runtime_root = ensure_private_directory(Path(runtime_root).expanduser().resolve())
        self.registry_path = self.runtime_root / "projects.json"
        self.worktree_root = ensure_private_directory(self.runtime_root / "worktrees")

    def _read_registry(self) -> dict[str, dict]:
        if not self.registry_path.exists():
            return {}
        ensure_private_file(self.registry_path)
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise GenericProjectError("generic project registry is unreadable") from error
        if not isinstance(payload, dict):
            raise GenericProjectError("generic project registry schema is invalid")
        return payload

    def _write_registry(self, payload: dict[str, dict]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        temporary = self.registry_path.with_name(
            f".{self.registry_path.name}.{os.getpid()}.tmp"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.registry_path)
            ensure_private_file(self.registry_path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _default_project_id(root: Path) -> str:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-._") or "project"
        digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
        value = f"{stem[:36]}-{digest}"
        if not PROJECT_ID_RE.fullmatch(value):
            raise GenericProjectError("unable to derive safe project id")
        return value

    def register(self, source_repo: str | Path, *, project_id: str | None = None) -> RegisteredProject:
        root = _explicit_git_root(source_repo)
        identifier = project_id or self._default_project_id(root)
        if not PROJECT_ID_RE.fullmatch(identifier):
            raise GenericProjectError("invalid project id")
        head = _git(root, "rev-parse", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise GenericProjectError("project HEAD is not a full commit SHA")
        profile = _detect_test_profile(root)
        record = RegisteredProject(identifier, str(root), head, profile.value, _utc_now())
        payload = self._read_registry()
        existing = payload.get(identifier)
        if existing:
            if existing.get("source_root") != str(root):
                raise GenericProjectError("project id already belongs to a different repository")
            return RegisteredProject(**existing)
        payload[identifier] = asdict(record)
        self._write_registry(payload)
        return record

    def get(self, project_id: str) -> RegisteredProject:
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise GenericProjectError("invalid project id")
        raw = self._read_registry().get(project_id)
        if raw is None:
            raise GenericProjectError("project is not registered")
        record = RegisteredProject(**raw)
        current = _explicit_git_root(record.source_root)
        if str(current) != record.source_root:
            raise GenericProjectError("registered project path changed")
        return record

    def list_projects(self) -> tuple[RegisteredProject, ...]:
        values = [RegisteredProject(**item) for item in self._read_registry().values()]
        return tuple(sorted(values, key=lambda item: item.project_id))

    def create_task_worktree(
        self,
        project_id: str,
        task_id: str,
        *,
        base_ref: str = "HEAD",
        test_profile: TestProfile | str | None = None,
    ) -> TaskWorktree:
        project = self.get(project_id)
        if not TASK_ID_RE.fullmatch(task_id):
            raise GenericProjectError("invalid task id")
        source = Path(project.source_root)
        base = _git(source, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        if not re.fullmatch(r"[0-9a-f]{40}", base):
            raise GenericProjectError("task base ref is not a full local commit SHA")

        branch = f"local-ai/{project_id}/{task_id}"
        exists = subprocess.run(
            ("/usr/bin/git", "-C", str(source), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=10,
            check=False,
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"},
        )
        if exists.returncode == 0:
            raise GenericProjectError("task branch already exists")
        if exists.returncode not in {0, 1}:
            raise GenericProjectError("unable to verify task branch availability")

        project_worktrees = ensure_private_directory(self.worktree_root / project_id)
        target = project_worktrees / task_id
        if target.exists() or target.is_symlink():
            raise GenericProjectError("task worktree path already exists")
        _git(source, "worktree", "add", "-b", branch, str(target), base, timeout=60)
        try:
            evidence = validate_workspace(target)
        except Exception as error:
            # Do not guess whether partial Git mutation is safe to remove. Leave
            # it for explicit owner reconciliation.
            raise GenericProjectError("created task worktree failed validation") from error
        if evidence.branch != branch:
            raise GenericProjectError("task worktree branch identity mismatch")

        if test_profile is None:
            profile = TestProfile(project.detected_test_profile)
        else:
            profile = TestProfile(str(test_profile))
        return TaskWorktree(
            project_id=project.project_id,
            task_id=task_id,
            source_root=project.source_root,
            worktree_root=str(evidence.root),
            branch=evidence.branch,
            base_commit_sha=base,
            test_profile=profile.value,
            created_at=_utc_now(),
        )
