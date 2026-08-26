from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_ai_control.services.generic_project_repository_guarded import (
    GuardedGenericProjectSupervisorRepository,
)
from local_ai_control.services.supervisor_contracts import WorkflowStage
from local_ai_control.services.supervisor_generic_project import (
    GenericProjectCodexTaskSpec,
    GenericProjectWorkflowSupervisor,
    create_generic_qwen_job,
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "external"
    root.mkdir()
    git(root, "init", "-b", "feat/generic-review-test")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "seed")
    return root


def prepare(tmp_path: Path):
    root = make_repo(tmp_path)
    repo = GuardedGenericProjectSupervisorRepository(root, tmp_path / "runtime/supervisor.db")
    repo.migrate()
    job, _unit = create_generic_qwen_job(
        repo,
        title="generic review policy",
        owner_id="owner",
        task_prompt="Change value() to return 2.",
        job_id="generic-policy-job",
    )
    (root / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    return root, repo, job


def test_generic_producer_reconstructs_from_job_baseline_identity(tmp_path):
    root = make_repo(tmp_path)
    repo = GuardedGenericProjectSupervisorRepository(root, tmp_path / "runtime/supervisor.db")
    repo.migrate()
    try:
        job, _unit = create_generic_qwen_job(
            repo,
            title="generic producer manifest",
            owner_id="owner",
            task_prompt="Change value() to return 2.",
            job_id="generic-producer-job",
        )
        reconstructed = repo.reconstruct_codex_task(
            job.job_id,
            job.owner_id,
            WorkflowStage.PRODUCER,
            0,
        )
        validated = reconstructed.validate()
        assert validated["safe_file_manifest"]
        assert any(item["path"] == "app.py" for item in validated["safe_file_manifest"])
        current = repo.candidate_identity_provider.snapshot(job.baseline_commit_sha)
        assert reconstructed.candidate_identity.same_candidate(current)
    finally:
        repo.close()


def test_generic_producer_reconstruct_fails_if_job_baseline_changed(tmp_path):
    root = make_repo(tmp_path)
    repo = GuardedGenericProjectSupervisorRepository(root, tmp_path / "runtime/supervisor.db")
    repo.migrate()
    try:
        job, _unit = create_generic_qwen_job(
            repo,
            title="generic producer stale candidate",
            owner_id="owner",
            task_prompt="Change value() to return 2.",
            job_id="generic-producer-stale-job",
        )
        (root / "app.py").write_text("def value():\n    return 99\n", encoding="utf-8")
        with pytest.raises(ValueError, match="producer baseline identity is stale"):
            repo.reconstruct_codex_task(
                job.job_id,
                job.owner_id,
                WorkflowStage.PRODUCER,
                0,
            )
    finally:
        repo.close()


def test_generic_review_manifest_accepts_exact_authorized_repo_root(tmp_path):
    root, repo, job = prepare(tmp_path)
    try:
        job = repo.update_job(job.job_id, current_stage=WorkflowStage.REVIEW)
        supervisor = GenericProjectWorkflowSupervisor(repo, {}, timeout_seconds=900)
        spec = supervisor._default_review_spec(job, 1)
        unit = repo.create_review_work_unit(
            job.job_id,
            job.owner_id,
            1,
            spec,
            review_work_unit_id="review-generic-policy-job-1",
        )
        assert "app.py" in unit.candidate_identity.candidate_paths
        patch = repo.content_store.get(unit.patch_content_ref, unit.patch_sha256)
        assert "app.py" in patch
        assert "return 2" in patch
    finally:
        repo.close()


def test_generic_revision_manifest_uses_same_generic_policy(tmp_path):
    root, repo, job = prepare(tmp_path)
    try:
        job = repo.update_job(
            job.job_id,
            current_stage=WorkflowStage.REVISION,
            review_round=1,
        )
        spec = GenericProjectCodexTaskSpec(
            root,
            (root,),
            "Apply the requested revision.",
            "LOW",
            60,
            "CODE",
            {"type": "object"},
            write_roots=(root,),
        )
        unit = repo.create_work_unit(
            job.job_id,
            job.owner_id,
            WorkflowStage.REVISION,
            spec,
            work_unit_id="revision-generic-policy-job-1",
            review_round=1,
        )
        assert "app.py" in unit.candidate_identity.candidate_paths
        assert any(item["path"] == "app.py" for item in unit.safe_file_manifest)
        reconstructed = repo.reconstruct_codex_task(
            job.job_id,
            job.owner_id,
            WorkflowStage.REVISION,
            1,
        )
        assert reconstructed.candidate_identity.same_candidate(unit.candidate_identity)
    finally:
        repo.close()


def test_generic_revision_reconstruct_fails_if_candidate_changes_after_unit(tmp_path):
    root, repo, job = prepare(tmp_path)
    try:
        job = repo.update_job(
            job.job_id,
            current_stage=WorkflowStage.REVISION,
            review_round=1,
        )
        spec = GenericProjectCodexTaskSpec(
            root,
            (root,),
            "Apply the requested revision.",
            "LOW",
            60,
            "CODE",
            {"type": "object"},
            write_roots=(root,),
        )
        repo.create_work_unit(
            job.job_id,
            job.owner_id,
            WorkflowStage.REVISION,
            spec,
            work_unit_id="revision-generic-policy-stale-1",
            review_round=1,
        )
        (root / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")
        with pytest.raises(ValueError, match="revision candidate identity is stale"):
            repo.reconstruct_codex_task(
                job.job_id,
                job.owner_id,
                WorkflowStage.REVISION,
                1,
            )
    finally:
        repo.close()
