from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import hashlib
import os
import re
import subprocess
from urllib.parse import urlparse

from local_ai_control.services.models import QWEN38


DEFAULT_BRIDGE_URL = "http://127.0.0.1:8010/v1"
DEFAULT_RUNTIME_ROOT = Path("/Users/jerson/AI/runtime/codex-qwen")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class WorkspacePolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkspaceEvidence:
    root: Path
    branch: str


def _run_git(root: Path, *args: str, runner=subprocess.run) -> str:
    try:
        result = runner(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise WorkspacePolicyError("git workspace probe failed") from error
    if result.returncode != 0:
        raise WorkspacePolicyError("workspace is not a readable git worktree")
    return result.stdout.strip()


def validate_workspace(path: str | Path, *, runner=subprocess.run) -> WorkspaceEvidence:
    raw = Path(path).expanduser()
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise WorkspacePolicyError("workspace does not exist") from error
    if not resolved.is_dir():
        raise WorkspacePolicyError("workspace must be a directory")

    top = Path(_run_git(resolved, "rev-parse", "--show-toplevel", runner=runner))
    try:
        top = top.resolve(strict=True)
    except OSError as error:
        raise WorkspacePolicyError("git worktree root is unavailable") from error
    if top != resolved:
        raise WorkspacePolicyError("workspace must be the explicit git worktree root")

    branch = _run_git(resolved, "branch", "--show-current", runner=runner)
    if not branch or branch in {"main", "master"}:
        raise WorkspacePolicyError("protected or detached branch denied")
    if not _SAFE_BRANCH.fullmatch(branch) or ".." in branch.split("/"):
        raise WorkspacePolicyError("unsafe branch name")
    return WorkspaceEvidence(resolved, branch)


def validate_bridge_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/v1")
    ):
        raise WorkspacePolicyError("bridge URL must be loopback HTTP ending in /v1")
    return url.rstrip("/")


def render_codex_config(bridge_url: str = DEFAULT_BRIDGE_URL) -> str:
    bridge_url = validate_bridge_url(bridge_url)
    return f'''model = "{QWEN38.model_id}"
model_provider = "qwen_local_bridge"
approval_policy = "never"
sandbox_mode = "workspace-write"

[model_providers.qwen_local_bridge]
name = "Qwen Local Responses Bridge"
base_url = "{bridge_url}"
wire_api = "responses"
requires_openai_auth = false

[sandbox_workspace_write]
network_access = false
'''


def prepare_codex_home(
    workspace: str | Path,
    *,
    bridge_url: str = DEFAULT_BRIDGE_URL,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    runner=subprocess.run,
) -> tuple[WorkspaceEvidence, Path]:
    evidence = validate_workspace(workspace, runner=runner)
    root = Path(runtime_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)

    digest = hashlib.sha256(str(evidence.root).encode("utf-8")).hexdigest()[:16]
    home = root / digest
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)

    config = home / "config.toml"
    temp = home / f".config.toml.{os.getpid()}.tmp"
    temp.write_text(render_codex_config(bridge_url), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(config)
    os.chmod(config, 0o600)
    return evidence, home


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare isolated CODEX_HOME for local Qwen producer"
    )
    parser.add_argument("workspace")
    parser.add_argument("--bridge-url", default=DEFAULT_BRIDGE_URL)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    args = parser.parse_args(argv)
    _evidence, home = prepare_codex_home(
        args.workspace,
        bridge_url=args.bridge_url,
        runtime_root=args.runtime_root,
    )
    print(str(home))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
