from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_ai_control.services.generic_project_adapter import GenericProjectError, GenericProjectRegistry
from local_ai_control.services.generic_project_policy import TestProfile
from local_ai_control.services.generic_project_operator import build_parser


def _git(root: Path, *args: str):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "sample"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Local AI Test")
    (root / "pyproject.toml").write_text("[project]\nname='sample'\nversion='0.0.1'\n", encoding="utf-8")
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "seed")
    return root


def test_register_detects_pytest_without_executing_project(tmp_path):
    source = _repo(tmp_path)
    registry = GenericProjectRegistry(tmp_path / "runtime")
    record = registry.register(source, project_id="sample")
    assert record.project_id == "sample"
    assert record.detected_test_profile == TestProfile.PYTEST.value
    assert record.source_root == str(source.resolve())


def test_create_task_worktree_is_isolated_feature_branch(tmp_path):
    source = _repo(tmp_path)
    registry = GenericProjectRegistry(tmp_path / "runtime")
    registry.register(source, project_id="sample")
    task = registry.create_task_worktree("sample", "fix-one")
    target = Path(task.worktree_root)
    assert target != source
    assert target.is_dir()
    assert task.branch == "local-ai/sample/fix-one"
    assert _git(target, "branch", "--show-current") == task.branch
    assert _git(source, "branch", "--show-current") == "main"


def test_duplicate_task_branch_fails_closed(tmp_path):
    source = _repo(tmp_path)
    registry = GenericProjectRegistry(tmp_path / "runtime")
    registry.register(source, project_id="sample")
    registry.create_task_worktree("sample", "fix-one")
    with pytest.raises(GenericProjectError):
        registry.create_task_worktree("sample", "fix-one")


def test_operator_exposes_single_task_and_review_flow():
    parser = build_parser()
    args = parser.parse_args([
        "task", "--project", "sample", "--task-id", "fix-one", "--prompt-file", "prompt.txt"
    ])
    assert args.command == "task"
    assert args.privacy == "RESTRICTED"
    assert args.risk == "LOW"
