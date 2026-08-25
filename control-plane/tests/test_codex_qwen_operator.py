from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_ai_control.services.supervisor_contracts import JobStatus, WorkflowJob, WorkflowStage
from local_ai_control.services import supervisor_local_qwen_operator as operator


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args), cwd=root, capture_output=True, text=True,
        shell=False, timeout=10, check=True,
    )
    return result.stdout.strip()


def feature_repo(tmp_path: Path, branch: str = "feat/operator-test") -> Path:
    root = tmp_path / "repo"
    (root / "control-plane" / "src").mkdir(parents=True)
    (root / "control-plane" / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "control-plane" / "src" / "seed.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "init", "-b", branch)
    git(root, "config", "user.email", "operator@example.invalid")
    git(root, "config", "user.name", "Operator Fixture")
    git(root, "add", ".")
    git(root, "commit", "-m", "seed")
    return root


def job_at(stage=WorkflowStage.INTAKE, status=JobStatus.QUEUED, resume=None):
    return WorkflowJob(
        "job", "safe title", "/tmp/repo", "now", "now", "owner", "LOW",
        status, stage, 0, 0, 2, 2, None, resume, "owner", {}, None,
        "a" * 40, True, "b" * 64,
    )


def test_operator_db_path_is_branch_bound_and_deterministic(tmp_path):
    root = feature_repo(tmp_path)
    runtime = tmp_path / "runtime"
    first = operator.operator_db_path(root, runtime)
    second = operator.operator_db_path(root, runtime)
    assert first == second
    assert first.parent.parent == runtime.resolve()
    git(root, "branch", "-m", "feat/operator-renamed")
    assert operator.operator_db_path(root, runtime) != first


def test_safe_status_payload_never_contains_private_prompt_or_metadata():
    payload = operator.safe_job_payload(job_at())
    assert payload["job_id"] == "job"
    assert "metadata" not in payload
    assert "prompt" not in json.dumps(payload).lower()


def test_prompt_file_rejects_symlink(tmp_path):
    target = tmp_path / "prompt.txt"
    target.write_text("fix the bounded implementation\n", encoding="utf-8")
    link = tmp_path / "prompt-link.txt"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        operator._load_prompt(str(link))
    assert operator._load_prompt(str(target)).startswith("fix the bounded")


def test_findings_parser_accepts_bounded_file_and_workflow_findings(tmp_path):
    path = tmp_path / "findings.json"
    path.write_text(json.dumps({"findings": [
        {
            "scope": "FILE", "severity": "HIGH", "file": "control-plane/src/example.py",
            "evidence": "wrong branch", "recommended_fix": "correct the condition",
        },
        {
            "scope": "WORKFLOW", "severity": "LOW", "file": None,
            "evidence": "missing explanation", "recommended_fix": "add a concise note",
        },
    ]}), encoding="utf-8")
    findings = operator._parse_findings(str(path))
    assert len(findings) == 2
    assert findings[0].scope == "FILE" and findings[0].file.endswith("example.py")
    assert findings[1].scope == "WORKFLOW" and findings[1].file is None


def test_findings_parser_rejects_symlink_and_empty_list(tmp_path):
    target = tmp_path / "findings.json"
    target.write_text('{"findings": []}', encoding="utf-8")
    link = tmp_path / "findings-link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        operator._parse_findings(str(link))
    with pytest.raises(ValueError, match="non-empty"):
        operator._parse_findings(str(target))


def test_run_until_boundary_stops_before_human_review_without_running_model():
    review_job = job_at(WorkflowStage.REVIEW, JobStatus.WAITING, "REVIEW_RESULT_PENDING")

    class Repo:
        @staticmethod
        def get_job(job_id):
            assert job_id == "job"
            return review_job

    class Supervisor:
        @staticmethod
        def run_job_once(_job_id):
            raise AssertionError("review boundary must stop before another runner call")

    assert operator.run_until_boundary(Supervisor(), Repo(), "job") is review_job


def test_revision_work_unit_is_created_from_durable_round(monkeypatch):
    revision_job = job_at(WorkflowStage.REVISION, JobStatus.QUEUED)
    object.__setattr__(revision_job, "review_round", 1)
    producer = SimpleNamespace(
        allowed_paths=(Path("/tmp/repo/control-plane"),),
        timeout_seconds=120,
        expected_output_schema={"type": "object"},
        write_roots=(Path("/tmp/repo/control-plane/src"),),
    )

    class Repo:
        repo_root = Path("/tmp/repo")
        created = None

        @staticmethod
        def work_unit_for_stage(*_args, **_kwargs):
            raise KeyError("missing")

        @staticmethod
        def reconstruct_codex_task(*_args, **_kwargs):
            return producer

        def create_work_unit(self, *args, **kwargs):
            self.created = (args, kwargs)
            return "revision-unit"

    repo = Repo()
    monkeypatch.setattr(operator, "_revision_prompt", lambda _repo, _job: "bounded revision")
    assert operator.ensure_revision_work_unit(repo, revision_job) == "revision-unit"
    args, kwargs = repo.created
    assert args[2] is WorkflowStage.REVISION
    assert kwargs["review_round"] == 1
    assert kwargs["work_unit_id"] == "revision-job-1"


def test_parser_requires_explicit_prompt_file_for_submit():
    parser = operator.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--workspace", "/tmp/repo", "submit"])
