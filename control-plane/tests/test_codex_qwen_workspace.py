import stat
import subprocess

import pytest

from local_ai_control.services.codex_qwen_workspace import (
    WorkspacePolicyError,
    prepare_codex_home,
    render_codex_config,
    validate_bridge_url,
    validate_workspace,
)


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def make_repo(tmp_path, branch="feat/local-producer"):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git("init", "-b", branch, cwd=repo)
    return repo


def test_feature_worktree_is_accepted(tmp_path):
    repo = make_repo(tmp_path)
    evidence = validate_workspace(repo)
    assert evidence.root == repo.resolve()
    assert evidence.branch == "feat/local-producer"


def test_main_and_subdirectory_are_denied(tmp_path):
    main = make_repo(tmp_path / "maincase", branch="main")
    with pytest.raises(WorkspacePolicyError):
        validate_workspace(main)

    feature = make_repo(tmp_path / "featurecase")
    sub = feature / "sub"
    sub.mkdir()
    with pytest.raises(WorkspacePolicyError, match="explicit git worktree root"):
        validate_workspace(sub)


def test_symlinked_workspace_is_denied(tmp_path):
    repo = make_repo(tmp_path / "real")
    link = tmp_path / "linked-workspace"
    link.symlink_to(repo, target_is_directory=True)
    with pytest.raises(WorkspacePolicyError, match="symlinked"):
        validate_workspace(link)


def test_bridge_url_must_be_loopback_v1():
    assert (
        validate_bridge_url("http://127.0.0.1:8010/v1")
        == "http://127.0.0.1:8010/v1"
    )
    for url in (
        "https://127.0.0.1:8010/v1",
        "http://example.com:8010/v1",
        "http://127.0.0.1:8010/",
        "http://user:pass@127.0.0.1:8010/v1",
        "http://127.0.0.1:8010/v1?x=1",
    ):
        with pytest.raises(WorkspacePolicyError):
            validate_bridge_url(url)


def test_config_explicitly_pins_model_provider_and_safe_sandbox():
    config = render_codex_config()
    assert 'model = "mlx-community/Qwen3.8-27B-8bit"' in config
    assert 'model_provider = "qwen_local_bridge"' in config
    assert 'base_url = "http://127.0.0.1:8010/v1"' in config
    assert 'wire_api = "responses"' in config
    assert "requires_openai_auth = false" in config
    assert 'approval_policy = "never"' in config
    assert 'sandbox_mode = "workspace-write"' in config
    assert "network_access = false" in config


def test_prepare_codex_home_is_private_and_does_not_touch_user_home(tmp_path):
    repo = make_repo(tmp_path / "workspace")
    runtime = tmp_path / "runtime"
    _evidence, home = prepare_codex_home(repo, runtime_root=runtime)
    config = home / "config.toml"
    assert config.exists()
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert home.parent == runtime.resolve()
