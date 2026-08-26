from __future__ import annotations

import subprocess
from pathlib import Path

from local_ai_control.services.direct_local_qwen_verified import (
    VerifiedDirectGenericProjectQwenRunner,
    VerifiedDirectProjectToolbox,
)
from local_ai_control.services.generic_project_policy import TestProfile
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
    git(root, "init", "-b", "feat/verified-direct-agent")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "seed")
    return root


class PrematureThenCorrectProvider:
    def __init__(self):
        self.responses = iter(
            [
                "<FINAL>Done.</FINAL>",
                '<TOOL>{"name":"read_file","path":"app.py"}</TOOL>',
                '<TOOL>{"name":"write_file","path":"app.py","content":"def value():\\n    return 2\\n"}</TOOL>',
                '<TOOL>{"name":"git_diff"}</TOOL>',
                "<FINAL>Updated app.py.</FINAL>",
            ]
        )
        self.prompts: list[str] = []

    def health(self):
        return {"status": "healthy", "model": "fake"}

    def generate(self, prompt, max_output_tokens=1024):
        self.prompts.append(prompt)
        return ModelReply(next(self.responses), "completed", None, 10, max_output_tokens)


class AlwaysPrematureProvider:
    def health(self):
        return {"status": "healthy", "model": "fake"}

    def generate(self, prompt, max_output_tokens=1024):
        return ModelReply("<FINAL>Done.</FINAL>", "completed", None, 10, max_output_tokens)


def task_spec(root: Path) -> GenericProjectCodexTaskSpec:
    return GenericProjectCodexTaskSpec(
        root,
        (root,),
        "Change value() to return 2.",
        "LOW",
        60,
        "CODE",
        {"type": "object"},
        write_roots=(root,),
    )


def test_premature_final_is_rejected_then_agent_can_finish(tmp_path):
    root = make_repo(tmp_path)
    provider = PrematureThenCorrectProvider()
    runner = VerifiedDirectGenericProjectQwenRunner(enabled=True, provider=provider)

    result = runner.run_task(task_spec(root), "00000000-0000-4000-8000-000000000011")

    assert result.status.value == "PASS"
    assert result.metrics["finalization_verified"] is True
    assert result.metrics["finalization_denials"] == 1
    assert result.metrics["candidate_diff_nonempty"] is True
    assert result.metrics["diff_verified_after_latest_write"] is True
    assert "finalization_denied" in provider.prompts[1]
    assert (root / "app.py").read_text(encoding="utf-8") == "def value():\n    return 2\n"


def test_repeated_empty_final_never_passes(tmp_path):
    root = make_repo(tmp_path)
    runner = VerifiedDirectGenericProjectQwenRunner(enabled=True, provider=AlwaysPrematureProvider())

    result = runner.run_task(task_spec(root), "00000000-0000-4000-8000-000000000012")

    assert result.status.value == "FAIL"
    assert result.metrics["finalization_verified"] is False
    assert git(root, "status", "--porcelain") == ""


def test_selected_test_profile_requires_post_write_test_evidence(tmp_path):
    root = make_repo(tmp_path)
    box = VerifiedDirectProjectToolbox(root, TestProfile.PYTEST)
    box.write_file("app.py", "def value():\n    return 2\n")
    box.git_diff()

    reasons = box.finalization_reasons()

    assert "fixed tests have not passed after the latest write" in reasons
