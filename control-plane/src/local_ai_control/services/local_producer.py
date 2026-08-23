"""Fail-closed local code producer for Qwen3.8.

The model proposes exact structured text replacements. This module validates the
plan, materializes a unified diff deterministically, and optionally applies it.
The model never receives shell, Git credentials, arbitrary filesystem access,
commit, push, merge, deploy, or service-control authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable
import uuid

from local_ai_control.services.qwen38_runtime import Qwen38Provider
from local_ai_control.services.security import SecretFirewall
from local_ai_control.services.supervisor_contracts import (
    AI_ROOT, CodexTaskSpec, RepoAccessPolicy, RepoWritePolicy, StageResult, StageResultStatus,
)

MAX_TASK_BYTES = 20_000
MAX_CONTEXT_BYTES = 24_000
MAX_FILE_CONTEXT_BYTES = 10_000
MAX_PATCH_BYTES = 256_000
MAX_PATCH_FILES = 8
MAX_MODEL_OUTPUT_TOKENS = 4096
MAX_EDITS = 12
MAX_EDIT_BLOCK_BYTES = 32_000
MAX_EDIT_TOTAL_BYTES = 128_000


class LocalProducerError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalPatchProposal:
    patch: str
    summary: str
    patch_sha256: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class ContextFile:
    path: str
    sha256: str
    content: str
    truncated: bool


def _access_policy(root: Path) -> RepoAccessPolicy:
    return RepoAccessPolicy(root, (root / "control-plane", root / "docs"))


def _git(repo_root: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.run(
        ("git", *args), cwd=repo_root, input=input_text, capture_output=True, text=True,
        shell=False, timeout=20, check=False, env=env,
    )


def require_safe_worktree(repo_root: Path = AI_ROOT) -> str:
    root = Path(repo_root).resolve()
    branch = _git(root, "branch", "--show-current")
    if branch.returncode != 0 or not branch.stdout.strip():
        raise LocalProducerError("unable to determine Git branch")
    name = branch.stdout.strip()
    if name == "main":
        raise LocalProducerError("local producer refuses to mutate main")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        raise LocalProducerError("unable to inspect Git worktree")
    if status.stdout.strip():
        raise LocalProducerError("local producer requires a clean worktree")
    return name


def _task_terms(task: str) -> tuple[str, ...]:
    terms = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,8}", task))
    stop = {"required", "current", "tests", "test", "model", "local", "production", "runtime", "please"}
    return tuple(sorted((term for term in terms if term.lower() not in stop), key=len, reverse=True)[:80])


def _bounded_file_excerpt(text: str, task: str, limit: int = MAX_FILE_CONTEXT_BYTES) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    lines = text.splitlines()
    lowered_terms = tuple(term.lower() for term in _task_terms(task))
    hits = [index for index, line in enumerate(lines) if any(term in line.lower() for term in lowered_terms)]
    if not hits:
        clipped = raw[:limit].decode("utf-8", errors="ignore")
        return clipped + "\n# [LOCAL_PRODUCER_CONTEXT_TRUNCATED]\n", True
    chosen: set[int] = set()
    for hit in hits:
        chosen.update(range(max(0, hit - 16), min(len(lines), hit + 17)))
    rendered: list[str] = []
    used = 0
    previous = -2
    for index in sorted(chosen):
        line = lines[index]
        encoded = (line + "\n").encode("utf-8")
        if used + len(encoded) > limit:
            break
        if index != previous + 1:
            marker = f"# [lines around {index + 1}]\n"
            marker_bytes = marker.encode("utf-8")
            if used + len(marker_bytes) > limit:
                break
            rendered.append(marker)
            used += len(marker_bytes)
        rendered.append(line + "\n")
        used += len(encoded)
        previous = index
    return "".join(rendered) + "# [LOCAL_PRODUCER_CONTEXT_EXCERPT]\n", True


def discover_context_paths(task: str, repo_root: Path = AI_ROOT) -> tuple[str, ...]:
    root = Path(repo_root).resolve()
    policy = _access_policy(root)
    candidates: list[str] = []
    for value in re.findall(r"(?:control-plane|docs)/[A-Za-z0-9_./-]+\.(?:py|md|json|toml|yaml|yml|txt)", task):
        candidates.append(value.rstrip(".,:;)]}"))
    low = task.lower()
    if any(word in low for word in ("heavy", "qwen3.8", "qwen38", "failover", "process", "runtime", "模型", "进程")):
        candidates += [
            "control-plane/src/local_ai_control/services/runtime_providers.py",
            "control-plane/src/local_ai_control/services/qwen38_runtime.py",
            "control-plane/tests/test_runtime_async_r3.py",
            "control-plane/src/local_ai_control/supervisor/process_identity.py",
        ]
    if "supervisor" in low or "工作流" in task:
        candidates += [
            "control-plane/src/local_ai_control/services/supervisor_codex.py",
            "control-plane/src/local_ai_control/services/supervisor.py",
        ]
    result: list[str] = []
    for value in candidates:
        if value in result:
            continue
        try:
            normalized = policy.validate_candidate_path(value, policy.default_allowed_paths)
        except (PermissionError, FileNotFoundError):
            continue
        if (root / normalized).is_file():
            result.append(normalized)
    return tuple(result[:12])


def build_context(task: str, paths: Iterable[str], repo_root: Path = AI_ROOT) -> tuple[ContextFile, ...]:
    root = Path(repo_root).resolve()
    policy = _access_policy(root)
    firewall = SecretFirewall()
    result: list[ContextFile] = []
    total = 0
    for value in paths:
        normalized = policy.validate_candidate_path(str(value), policy.default_allowed_paths)
        path = root / normalized
        if path.is_symlink() or not path.is_file():
            raise LocalProducerError(f"unsafe context path: {normalized}")
        data = path.read_bytes()
        if b"\0" in data:
            raise LocalProducerError(f"binary context denied: {normalized}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LocalProducerError(f"non-UTF8 context denied: {normalized}") from exc
        if firewall.inspect(text).action == "BLOCK":
            raise LocalProducerError(f"secret-bearing context denied: {normalized}")
        excerpt, truncated = _bounded_file_excerpt(text, task)
        encoded = excerpt.encode("utf-8")
        if total + len(encoded) > MAX_CONTEXT_BYTES:
            continue
        total += len(encoded)
        result.append(ContextFile(normalized, hashlib.sha256(data).hexdigest(), excerpt, truncated))
    if not result:
        raise LocalProducerError("no safe context files selected")
    return tuple(result)


def build_prompt(task: str, context: tuple[ContextFile, ...], feedback: str | None = None) -> str:
    task_bytes = task.encode("utf-8")
    if not task_bytes or len(task_bytes) > MAX_TASK_BYTES:
        raise LocalProducerError("task prompt outside safe size bound")
    if SecretFirewall().inspect(task).action == "BLOCK":
        raise LocalProducerError("task rejected by Secret Firewall")
    files = []
    for item in context:
        files.append(
            f"\n<FILE path={json.dumps(item.path)} sha256={item.sha256} truncated={str(item.truncated).lower()}>\n"
            f"{item.content}\n</FILE>\n"
        )
    repair = f"\nPrevious deterministic validation error:\n{feedback[:4000]}\n" if feedback else ""
    prompt = (
        "You are Local Producer V0.2. Make the smallest correct code change for the task.\n"
        "You have NO shell, Git, filesystem, network, secrets, deployment, or service-control authority.\n"
        "Return exactly one JSON object and nothing else. Schema:\n"
        '{"summary":"short summary","edits":[{"path":"repo/relative/file.py","old":"exact existing text","new":"replacement text"}]}\n'
        "Edit rules: use only files shown in SAFE READ CONTEXT; existing text files only; 1-12 edits; at most 8 files. "
        "Each old value must be a non-empty EXACT UNIQUE substring copied verbatim from the provided file context. "
        "Keep old blocks as small as possible while still unique. The deterministic host will create the unified diff. "
        "Do not emit diff syntax, line numbers, new files, renames, deletes, shell commands, or tests-run claims. "
        "If multiple edits touch one file, order them so each old block matches after earlier replacements.\n\nTASK:\n"
        + task + repair + "\nSAFE READ CONTEXT:\n" + "".join(files)
    )
    if len(prompt.encode("utf-8")) > 48_000:
        raise LocalProducerError("assembled local-producer prompt exceeds safe 16K planning envelope")
    return prompt


def _parse_model_json(text: str) -> dict:
    value = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", value, flags=re.I)
    if fenced:
        value = fenced.group(1).strip()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LocalProducerError("model did not return strict JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"summary", "edits"}:
        raise LocalProducerError("model JSON schema mismatch")
    if not isinstance(payload["summary"], str) or len(payload["summary"]) > 2000:
        raise LocalProducerError("model summary invalid")
    edits = payload["edits"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
        raise LocalProducerError("model edits count outside safe bound")
    total = 0
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "old", "new"}:
            raise LocalProducerError("model edit schema mismatch")
        if not all(isinstance(edit[key], str) for key in ("path", "old", "new")):
            raise LocalProducerError("model edit values must be strings")
        if not edit["old"] or edit["old"] == edit["new"]:
            raise LocalProducerError("model edit must replace non-empty text")
        block_bytes = len(edit["old"].encode()) + len(edit["new"].encode())
        if block_bytes > MAX_EDIT_BLOCK_BYTES:
            raise LocalProducerError("model edit block too large")
        total += block_bytes
        if total > MAX_EDIT_TOTAL_BYTES:
            raise LocalProducerError("model edits total too large")
    return payload


def _validate_patch_section(section: str, source: str, target: str) -> None:
    prefix = section.split("@@", 1)[0]
    minus = re.findall(r"^--- (.+)$", prefix, flags=re.M)
    plus = re.findall(r"^\+\+\+ (.+)$", prefix, flags=re.M)
    if len(minus) != 1 or len(plus) != 1:
        raise LocalProducerError("patch metadata headers are malformed")
    expected_old = {f"a/{source}", "/dev/null"}
    if minus[0] not in expected_old or plus[0] != f"b/{target}":
        raise LocalProducerError("patch metadata path mismatch")
    if minus[0] == "/dev/null" and "new file mode " not in prefix:
        raise LocalProducerError("new file patch missing mode metadata")
    if minus[0] != "/dev/null" and "new file mode " in prefix:
        raise LocalProducerError("existing file patch has new-file metadata")


def validate_patch(patch: str, repo_root: Path = AI_ROOT, write_roots: tuple[Path, ...] = ()) -> tuple[str, ...]:
    root = Path(repo_root).resolve()
    encoded = patch.encode("utf-8")
    if not encoded or len(encoded) > MAX_PATCH_BYTES or b"\0" in encoded:
        raise LocalProducerError("patch outside safe size bound")
    forbidden = (
        "GIT binary patch", "Binary files ", "rename from ", "rename to ", "copy from ", "copy to ",
        "deleted file mode", "old mode ", "new mode ", "similarity index ", "dissimilarity index ",
    )
    if any(marker in patch for marker in forbidden):
        raise LocalProducerError("unsupported patch operation")
    matches = list(re.finditer(r"^diff --git a/(.+) b/(.+)$", patch, flags=re.M))
    if not matches or len(matches) > MAX_PATCH_FILES:
        raise LocalProducerError("patch file count outside safe bound")
    if patch[:matches[0].start()].strip():
        raise LocalProducerError("content before first diff header denied")
    targets: list[str] = []
    policy = RepoWritePolicy(root, write_roots)
    for index, match in enumerate(matches):
        source, target = match.group(1), match.group(2)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(patch)
        section = patch[match.start():end]
        if source != target:
            raise LocalProducerError("rename/copy style patch denied")
        if source.startswith("/") or ".." in Path(source).parts:
            raise LocalProducerError("patch path traversal denied")
        try:
            validated = policy.validate_git_ownership(target)
        except PermissionError as exc:
            raise LocalProducerError(str(exc)) from exc
        relative = validated.relative_to(root).as_posix()
        if relative != target:
            raise LocalProducerError("non-canonical patch path denied")
        _validate_patch_section(section, source, target)
        targets.append(relative)
    if len(set(targets)) != len(targets):
        raise LocalProducerError("duplicate patch target denied")
    return tuple(targets)


def materialize_edits(
    edits: list[dict], context: tuple[ContextFile, ...], repo_root: Path = AI_ROOT,
    write_roots: tuple[Path, ...] = (),
) -> str:
    root = Path(repo_root).resolve()
    context_by_path = {item.path: item for item in context}
    policy = RepoWritePolicy(root, write_roots)
    firewall = SecretFirewall()
    originals: dict[str, str] = {}
    current: dict[str, str] = {}

    for edit in edits:
        path_value = edit["path"]
        item = context_by_path.get(path_value)
        if item is None:
            raise LocalProducerError(f"edit path was not supplied as safe context: {path_value}")
        try:
            validated = policy.validate_git_ownership(path_value)
        except PermissionError as exc:
            raise LocalProducerError(str(exc)) from exc
        relative = validated.relative_to(root).as_posix()
        if relative != path_value:
            raise LocalProducerError("non-canonical edit path denied")
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise LocalProducerError(f"edit target must be existing regular file: {relative}")
        if relative not in originals:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != item.sha256:
                raise LocalProducerError(f"context snapshot changed before edit materialization: {relative}")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise LocalProducerError(f"non-UTF8 edit target denied: {relative}") from exc
            originals[relative] = text
            current[relative] = text
        old = edit["old"]
        new = edit["new"]
        if "\0" in old or "\0" in new:
            raise LocalProducerError("NUL in structured edit denied")
        if firewall.inspect(new).action == "BLOCK":
            raise LocalProducerError(f"secret-like replacement denied: {relative}")
        count = current[relative].count(old)
        if count != 1:
            raise LocalProducerError(
                f"old block must match exactly once in {relative}; matched {count} times. "
                "Copy a smaller exact unique block from SAFE READ CONTEXT."
            )
        current[relative] = current[relative].replace(old, new, 1)

    if not current:
        raise LocalProducerError("structured edit plan produced no files")
    parts: list[str] = []
    for relative in sorted(current):
        before = originals[relative]
        after = current[relative]
        if before == after:
            raise LocalProducerError(f"structured edits produced no change: {relative}")
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{relative}", tofile=f"b/{relative}", n=3,
        ))
        if not diff:
            raise LocalProducerError(f"unable to materialize diff: {relative}")
        parts.append(f"diff --git a/{relative} b/{relative}\n{diff}")
    patch = "".join(parts)
    validate_patch(patch, root, write_roots)
    return patch


def check_patch(patch: str, repo_root: Path = AI_ROOT) -> None:
    completed = _git(Path(repo_root).resolve(), "apply", "--check", "--whitespace=error-all", "-", input_text=patch)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise LocalProducerError("git apply check failed: " + detail[:3000])


def apply_patch(patch: str, repo_root: Path = AI_ROOT) -> None:
    check_patch(patch, repo_root)
    completed = _git(Path(repo_root).resolve(), "apply", "--whitespace=error-all", "-", input_text=patch)
    if completed.returncode != 0:
        raise LocalProducerError("git apply failed after successful check")


class LocalPatchProducer:
    def __init__(self, provider=None, *, repo_root: Path = AI_ROOT, write_roots: tuple[Path, ...] = ()):
        self.provider = provider or Qwen38Provider()
        self.repo_root = Path(repo_root).resolve()
        self.write_roots = write_roots

    def propose(self, task: str, paths: Iterable[str], *, attempts: int = 2) -> LocalPatchProposal:
        context = build_context(task, paths, self.repo_root)
        feedback = None
        last_error: Exception | None = None
        for _ in range(max(1, min(attempts, 3))):
            prompt = build_prompt(task, context, feedback)
            reply = self.provider.generate(prompt, max_output_tokens=MAX_MODEL_OUTPUT_TOKENS)
            if not reply.complete or not reply.text:
                last_error = LocalProducerError("local model response incomplete")
                feedback = str(last_error)
                continue
            try:
                payload = _parse_model_json(reply.text)
                patch = materialize_edits(payload["edits"], context, self.repo_root, self.write_roots)
                paths_out = validate_patch(patch, self.repo_root, self.write_roots)
                check_patch(patch, self.repo_root)
                return LocalPatchProposal(
                    patch, payload["summary"].strip(), hashlib.sha256(patch.encode()).hexdigest(), paths_out,
                )
            except LocalProducerError as exc:
                last_error = exc
                feedback = str(exc)
        raise LocalProducerError(f"local producer failed after bounded repair attempts: {last_error}")

    def propose_auto(self, task: str, *, attempts: int = 2) -> LocalPatchProposal:
        return self.propose(task, discover_context_paths(task, self.repo_root), attempts=attempts)

    def apply(self, proposal: LocalPatchProposal) -> None:
        validate_patch(proposal.patch, self.repo_root, self.write_roots)
        apply_patch(proposal.patch, self.repo_root)


class LocalProducerTaskRunner:
    """CodexTaskRunner-compatible adapter for the durable Supervisor boundary."""
    cancellation_supported = False

    def __init__(self, provider=None, *, attempts: int = 2):
        self.provider = provider or Qwen38Provider()
        self.attempts = attempts

    def cancel(self, execution_id: str | None = None, reason: str | None = None) -> bool:
        return False

    def run_task(self, spec: CodexTaskSpec, execution_id: str) -> StageResult:
        try:
            uuid.UUID(execution_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("execution_id must be a canonical UUID") from exc
        validated = spec.validate()
        require_safe_worktree(spec.repo_root)
        manifest_paths = {str(item["path"]) for item in validated["safe_file_manifest"]}
        discovered = discover_context_paths(spec.task_prompt, spec.repo_root)
        selected = tuple(path for path in discovered if path in manifest_paths)
        if not selected:
            selected = tuple(sorted(manifest_paths))[:6]
        producer = LocalPatchProducer(
            self.provider, repo_root=spec.repo_root,
            write_roots=tuple(Path(path) for path in validated["write_roots"]),
        )
        try:
            proposal = producer.propose(spec.task_prompt, selected, attempts=self.attempts)
            producer.apply(proposal)
        except LocalProducerError as exc:
            return StageResult(
                StageResultStatus.BLOCKED, "Local Producer could not produce a safe applicable patch",
                error=type(exc).__name__, metrics={"detail_sha256": hashlib.sha256(str(exc).encode()).hexdigest()},
            )
        return StageResult.passed(
            "Local Qwen3.8 produced and applied a deterministic-policy-validated patch; tests not yet run",
            artifacts=({"kind":"local_patch","reference":f"sha256:{proposal.patch_sha256}","size_bytes":len(proposal.patch.encode())},),
            metrics={"provider":"Qwen3.8-27B-8bit","files_changed":len(proposal.paths),"tests_executed":False},
        )