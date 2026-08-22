from pathlib import Path

import pytest

from local_ai_control.services.supervisor import (
    AI_ROOT, ReviewResult, ReviewTaskSpec, SupervisorRepository, WorkflowStage,
)
import local_ai_control.services.supervisor_round2 as round2
from local_ai_control.supervisor import process_identity


def test_round2_followup_nested_metadata_is_hashed_before_persistence(tmp_path):
    raw_prompt = "safe private project instructions"
    raw_authorization = "synthetic authorization value"
    repository = SupervisorRepository(tmp_path / "supervisor.db")
    repository.migrate()
    job = repository.create_job(
        "metadata",
        "owner",
        job_id="r2-meta-followup",
        metadata={
            "task": {"prompt": raw_prompt},
            "deep": [{"nested": {"authorization": raw_authorization}}],
            "normal": {"value": "readable"},
        },
    )
    stored = repository.db.execute(
        "SELECT metadata_json FROM supervisor_jobs WHERE job_id=?", (job.job_id,)
    ).fetchone()[0]
    assert raw_prompt not in stored
    assert raw_authorization not in stored
    assert "prompt_sha256" in stored and "authorization_sha256" in stored
    assert "readable" in stored
    repository.close()


def test_round2_followup_review_result_lifecycle_and_round_bound(tmp_path):
    repository = SupervisorRepository(tmp_path / "supervisor.db")
    repository.migrate()
    job = repository.create_job("review", "owner", job_id="r2-review-followup", max_review_rounds=2)
    repository.update_job(job.job_id, current_stage=WorkflowStage.REVIEW)
    spec = ReviewTaskSpec(
        AI_ROOT,
        (AI_ROOT / "control-plane", AI_ROOT / "docs"),
        "safe durable reviewer task",
        True,
        "LOW",
        60,
        "REVIEW",
        round2.REVIEW_RESULT_SCHEMA,
    )
    unit = repository.create_review_work_unit(job.job_id, "owner", 1, spec, "r2-review-unit")
    repository.submit_review_result(job.job_id, "owner", 1, unit.review_work_unit_id, ReviewResult("PASS"))
    assert repository.get_review_work_unit(unit.review_work_unit_id, job.job_id, "owner", 1).status == "RESULT_SUBMITTED"
    repository.mark_review_result_consumed(job.job_id, "owner", 1, unit.review_work_unit_id)
    assert repository.get_review_work_unit(unit.review_work_unit_id, job.job_id, "owner", 1).status == "CONSUMED"
    result_status = repository.db.execute(
        "SELECT status FROM supervisor_review_results WHERE review_work_unit_id=?", (unit.review_work_unit_id,)
    ).fetchone()[0]
    assert result_status == "CONSUMED"
    with pytest.raises(ValueError):
        repository.create_review_work_unit(job.job_id, "owner", 3, spec, "r2-review-too-late")
    repository.close()


def test_round2_followup_startup_identity_classification_and_cleanup_policy(monkeypatch):
    exact = process_identity.ProcessIdentity(
        123,
        str(process_identity.CONTROL_PLANE_PYTHON),
        process_identity.EXPECTED_ARGV,
        "START-1",
    )
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: None)
    assert process_identity.classify_started_process(123) == ("DEAD", None)
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: exact)
    assert process_identity.classify_started_process(123) == ("EXPECTED", "START-1")
    mismatch = process_identity.ProcessIdentity(123, "/other", ("other",), "START-2")
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: mismatch)
    assert process_identity.classify_started_process(123) == ("MISMATCH", None)

    source = (AI_ROOT / "control-plane/scripts/start-supervisor.sh").read_text()
    assert "START_RC -eq 3" in source
    assert "cleanup-start" in source
    assert "ORPHAN_RECONCILIATION_REQUIRED" in source
    assert "umask 077" in source
    assert "pkill" not in source and "killall" not in source and "kill -9" not in source
