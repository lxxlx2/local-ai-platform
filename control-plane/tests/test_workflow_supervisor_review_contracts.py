import hashlib

import pytest

from local_ai_control.services.supervisor import (
    AI_ROOT, CONTROL_PLANE_ROOT, CandidateIdentity, CodexTaskSpec, JobStatus,
    ReviewFinding, ReviewResult, ReviewTaskSpec, StageContext, StageResultStatus,
    SupervisorRepository, WorkflowStage,
)
from local_ai_control.services.supervisor_round2 import REVIEW_RESULT_SCHEMA


class EmptyCandidateProvider:
    def __init__(self, identity=None):
        self.identity = identity or CandidateIdentity(
            "COMMIT", "a" * 40, "b" * 40, "a" * 40, "c" * 64,
            "2026-08-22T00:00:00+00:00", (), (), (), (),
        )

    def snapshot(self, base_commit_sha=None):
        return self.identity

    def capture_baseline(self):
        return self.identity.base_commit_sha

    def worktree_is_clean(self):
        return True

    def unowned_write_root_paths(self, write_roots=()):
        return ()

    def build_review_patch(self, identity):
        return "# LOCAL_AI_SUPERVISOR_NO_CANDIDATE_CHANGES\n"


def repository(tmp_path, provider=None):
    repo = SupervisorRepository(
        tmp_path / "supervisor.db", candidate_identity_provider=provider or EmptyCandidateProvider(),
    )
    repo.migrate()
    return repo


def review_spec():
    return ReviewTaskSpec(
        AI_ROOT, (CONTROL_PLANE_ROOT,), "independently verify the immutable task objective",
        True, "LOW", 60, "REVIEW", REVIEW_RESULT_SCHEMA,
    )


def producer_then_review(repo, job_id="objective-job"):
    job = repo.create_job("objective contract", "owner", job_id=job_id)
    producer = repo.create_work_unit(
        job.job_id, "owner", WorkflowStage.PRODUCER,
        CodexTaskSpec(
            AI_ROOT, (CONTROL_PLANE_ROOT,),
            "Implement the requested durable objective and satisfy every acceptance criterion.",
            "LOW", 60, "CODE", {"type": "object", "required": ["status"]},
        ),
        f"producer-{job_id}",
    )
    job = repo.update_job(job.job_id, current_stage=WorkflowStage.REVIEW)
    review = repo.create_review_work_unit(job.job_id, "owner", 1, review_spec(), f"review-{job_id}-1")
    return job, producer, review


def test_empty_candidate_accepts_pass_and_workflow_fail_but_rejects_file_scope(tmp_path):
    repo = repository(tmp_path)
    job, _, unit = producer_then_review(repo)
    passed = repo.submit_review_result(job.job_id, "owner", 1, unit.review_work_unit_id, ReviewResult("PASS"))
    assert passed.status == "SUBMITTED"

    second = repository(tmp_path / "second")
    job2, _, unit2 = producer_then_review(second, "workflow-fail")
    workflow_finding = ReviewFinding(
        "HIGH", None, "Producer made no change and did not satisfy the objective.",
        "Implement the objective before requesting review.", "WORKFLOW",
    )
    submitted = second.submit_review_result(
        job2.job_id, "owner", 1, unit2.review_work_unit_id,
        ReviewResult("FAIL", (workflow_finding,)),
    )
    assert submitted.status == "SUBMITTED"
    restored = second.submitted_review_result(job2.job_id, "owner", 1, unit2.review_work_unit_id)
    assert restored.findings[0].scope == "WORKFLOW" and restored.findings[0].file is None
    with pytest.raises(PermissionError):
        second.submit_review_result(
            job2.job_id, "owner", 1, unit2.review_work_unit_id,
            ReviewResult("FAIL", (ReviewFinding("HIGH", "docs/arbitrary.md", "e", "f"),)),
        )
    repo.close(); second.close()


def test_empty_candidate_file_finding_and_workflow_path_bypass_are_rejected(tmp_path):
    repo = repository(tmp_path)
    job, _, unit = producer_then_review(repo)
    with pytest.raises(PermissionError, match="candidate manifest"):
        repo.submit_review_result(
            job.job_id, "owner", 1, unit.review_work_unit_id,
            ReviewResult("FAIL", (ReviewFinding(
                "HIGH", "control-plane/src/local_ai_control/services/supervisor_contracts.py", "e", "f",
            ),)),
        )
    with pytest.raises(ValueError, match="cannot reference"):
        repo.submit_review_result(
            job.job_id, "owner", 1, unit.review_work_unit_id,
            ReviewResult("FAIL", (ReviewFinding(
                "HIGH", "docs/ARCHITECTURE.md", "e", "f", "WORKFLOW",
            ),)),
        )
    repo.close()


def test_nonempty_file_finding_remains_valid_and_defaults_to_file_scope(tmp_path):
    identity = CandidateIdentity(
        "TREE_MANIFEST", None, "b" * 64, "a" * 40, "c" * 64,
        "2026-08-22T00:00:00+00:00",
        ("control-plane/src/local_ai_control/services/supervisor_contracts.py",), (), (), (),
    )
    repo = repository(tmp_path, EmptyCandidateProvider(identity))
    job, _, unit = producer_then_review(repo)
    finding = ReviewFinding(
        "HIGH", "control-plane/src/local_ai_control/services/supervisor_contracts.py", "evidence", "fix",
    )
    repo.submit_review_result(job.job_id, "owner", 1, unit.review_work_unit_id,
                              ReviewResult("FAIL", (finding,)))
    restored = repo.submitted_review_result(job.job_id, "owner", 1, unit.review_work_unit_id)
    assert restored.findings[0].scope == "FILE"
    repo.close()


def test_review_objective_is_private_restart_stable_and_tamper_evident(tmp_path):
    repo = repository(tmp_path)
    job, producer, unit = producer_then_review(repo)
    task = repo.reconstruct_reviewer_task(job.job_id, "owner", 1)
    assert task.task_objective.goal.startswith("Implement the requested durable objective")
    assert task.task_objective.source_work_unit_id == producer.work_unit_id
    assert task.objective_sha256 == unit.objective_sha256
    raw_objective = task.task_objective.goal
    assert raw_objective.encode() not in (tmp_path / "supervisor.db").read_bytes()
    repo.close()

    reopened = repository(tmp_path)
    recovered = reopened.reconstruct_reviewer_task(job.job_id, "owner", 1)
    assert recovered.task_objective == task.task_objective
    objective_path = reopened.content_store._path(unit.objective_content_ref)
    objective_path.write_text('{"goal":"tampered"}')
    with pytest.raises(ValueError, match="integrity"):
        reopened.reconstruct_reviewer_task(job.job_id, "owner", 1)
    reopened.close()


def test_revision_cannot_replace_original_objective_and_consumes_workflow_finding(tmp_path):
    repo = repository(tmp_path)
    job, _, first_review = producer_then_review(repo)
    finding = ReviewFinding("HIGH", None, "objective not satisfied", "produce required artifact", "WORKFLOW")
    result = ReviewResult("FAIL", (finding,))
    repo.submit_review_result(job.job_id, "owner", 1, first_review.review_work_unit_id, result)
    stage_result = result.to_stage_result()
    assert stage_result.status is StageResultStatus.FAIL
    repo.persist_review_findings(job.job_id, "owner", 1, stage_result.review_findings)
    job = repo.update_job(job.job_id, current_stage=WorkflowStage.REVISION, review_round=1)
    revision = repo.create_work_unit(
        job.job_id, "owner", WorkflowStage.REVISION,
        CodexTaskSpec(AI_ROOT, (CONTROL_PLANE_ROOT,), "Replace the objective maliciously", "LOW", 60, "CODE", {}),
        "revision-objective-job", review_round=1,
    )
    context = StageContext(job, WorkflowStage.REVISION, 1, "revision-token", 60, repo)
    persisted = context.current_review_findings()
    assert persisted[0].scope == "WORKFLOW" and persisted[0].file is None
    repo.mark_review_findings_consumed(job.job_id, "owner", 1, revision.work_unit_id)
    job = repo.update_job(job.job_id, current_stage=WorkflowStage.REVIEW)
    second_review = repo.create_review_work_unit(job.job_id, "owner", 2, review_spec(), "review-objective-job-2")
    assert second_review.objective_sha256 == first_review.objective_sha256
    second_task = repo.reconstruct_reviewer_task(job.job_id, "owner", 2)
    assert second_task.task_objective.goal.startswith("Implement the requested durable objective")
    assert "maliciously" not in second_task.task_objective.goal
    repo.close()


def test_objective_manifest_is_bound_to_review_work_unit_hash(tmp_path):
    repo = repository(tmp_path)
    job, _, unit = producer_then_review(repo)
    original_hash = unit.spec_hash
    with repo.db:
        repo.db.execute(
            "UPDATE supervisor_review_work_units SET objective_manifest_hash=? WHERE review_work_unit_id=?",
            (hashlib.sha256(b"stale").hexdigest(), unit.review_work_unit_id),
        )
    with pytest.raises(ValueError, match="objective manifest integrity"):
        repo.get_review_work_unit(unit.review_work_unit_id, job.job_id, "owner", 1)
    assert original_hash
    repo.close()
