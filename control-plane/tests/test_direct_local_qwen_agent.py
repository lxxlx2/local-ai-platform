from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_ai_control.services.direct_local_qwen_agent import (
    DirectGenericProjectQwenRunner,
    DirectLocalQwenProtocolError,
    DirectProjectToolbox,
    parse_direct_agent_action,
)
from local_ai_control.services.omlx import ModelReply
from local_ai_control.services.supervisor_generic_project import GenericProjectCodexTaskSpec


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "feat/direct-agent-test")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "seed")
    return root


class FakeProvider:
    def __init__(self):
        self.responses = iter([
            '<TOOL>{"name":"read_file","path":"app.py"}</TOOL>',
            '<TOOL>{"name":"write_file","path":"app.py","content":"def value():\\n    return 2\\n"}</TOOL>',
            '<TOOL>{"name":"git_diff"}</TOOL>',
            '<FINAL>Updated app.py with the requested minimal change.</FINAL>',
        ])

    def health(self):
        return {"status": "healthy", "model": "fake"}

    def generate(self, prompt, max_output_tokens=1024):
        return ModelReply(next(self.responses), "completed", None, 10, max_output_tokens)


def test_direct_action_parser_is_strict():
    action = parse_direct_agent_action('<TOOL>{"name":"git_diff"}</TOOL>')
    assert action.kind == "TOOL"
    assert action.payload["name"] == "git_diff"
    assert parse_direct_agent_action("<FINAL>done</FINAL>").kind == "FINAL"
    with pytest.raises(DirectLocalQwenProtocolError):
        parse_direct_agent_action("please run git diff")


def test_toolbox_rejects_path_escape(tmp_path):
    root = make_repo(tmp_path)
    box = DirectProjectToolbox(root)
    with pytest.raises(PermissionError):
        box.read_file("../outside.txt")


def test_direct_runner_modifies_feature_worktree_without_codex_cli(tmp_path):
    root = make_repo(tmp_path)
    spec = GenericProjectCodexTaskSpec(
        root,
        (root,),
        "Change value() to return 2.",
        "LOW",
        60,
        "CODE",
        {"type": "object"},
        write_roots=(root,),
    )
    runner = DirectGenericProjectQwenRunner(enabled=True, provider=FakeProvider())
    result = runner.run_task(spec, "00000000-0000-4000-8000-000000000001")
    assert result.status.value == "PASS"
    assert result.metrics["codex_cli_invoked"] is False
    assert result.metrics["network_access"] is False
    assert (root / "app.py").read_text(encoding="utf-8") == "def value():\n    return 2\n"
    assert git(root, "status", "--porcelain") == " M app.py"
