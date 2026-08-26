import sqlite3
from types import SimpleNamespace

from local_ai_control.services.cloud_egress import CloudEgressDecision, EgressAction
from local_ai_control.services.gemini_provider import GeminiReviewResult
from local_ai_control.services.gemini_review_gateway import GatedGeminiReviewResult
from local_ai_control.services.provider_router import PrivacyMode
from local_ai_control.services.supervisor_contracts import StageContext, StageResultStatus, WorkflowStage
from local_ai_control.services.supervisor_gemini_review import (
    GeminiAdvisoryReviewRunner,
    load_gemini_recommendation,
)


class FakeGateway:
    def __init__(self):
        self.calls = []

    def review(self, *, material, privacy):
        self.calls.append((material, privacy))
        return GatedGeminiReviewResult(
            review=GeminiReviewResult(
                verdict="PASS",
                summary="candidate looks correct",
                findings=(),
                model="gemini-test",
                latency_seconds=0.01,
            ),
            egress=CloudEgressDecision(
                action=EgressAction.SANITIZED,
                privacy=privacy,
                material=material,
                redactions=("email",),
                reason="restricted_minimized",
                original_sha256="1" * 64,
                material_sha256="2" * 64,
            ),
        )


class FakeRepository:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.unit = SimpleNamespace(
            review_work_unit_id="review-1",
            patch_sha256="a" * 64,
        )
        self.submitted_calls = 0

    def review_work_unit_for_round(self, job_id, owner_id, review_round):
        assert (job_id, owner_id, review_round) == ("job-1", "owner", 1)
        return self.unit

    def reconstruct_reviewer_task(self, job_id, owner_id, review_round):
        return SimpleNamespace(task_prompt="independent review", task_objective=None)

    def reconstruct_reviewer_patch(self, job_id, owner_id, review_round):
        return "diff --git a/app.py b/app.py\n+return 1\n"

    def submitted_review_result(self, job_id, owner_id, review_round, review_work_unit_id):
        self.submitted_calls += 1
        raise KeyError("human review not submitted")


def _context(repository, privacy):
    job = SimpleNamespace(
        job_id="job-1",
        owner_id="owner",
        review_round=0,
        project_scope="/tmp/project",
        metadata={"privacy_mode": privacy.value},
    )
    return StageContext(
        job=job,
        stage=WorkflowStage.REVIEW,
        attempt=1,
        idempotency_key="review-attempt-1",
        timeout_seconds=30,
        repository=repository,
    )


def test_gemini_advisory_is_persisted_once_and_does_not_auto_approve(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    repository = FakeRepository()
    gateway = FakeGateway()
    runner = GeminiAdvisoryReviewRunner(gateway=gateway)

    first = runner.run(_context(repository, PrivacyMode.RESTRICTED))
    second = runner.run(_context(repository, PrivacyMode.RESTRICTED))

    assert first.status is StageResultStatus.BLOCKED
    assert first.error == "REVIEW_RESULT_PENDING"
    assert first.metrics["gemini_advisory_status"] == "READY"
    assert first.metrics["gemini_verdict"] == "PASS"
    assert second.status is StageResultStatus.BLOCKED
    assert len(gateway.calls) == 1
    assert repository.submitted_calls == 2

    stored = load_gemini_recommendation(repository, "review-1")
    assert stored["verdict"] == "PASS"
    assert stored["patch_sha256"] == "a" * 64
    assert stored["egress_material_sha256"] == "2" * 64


def test_boundary_advisory_does_not_consume_human_review(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    repository = FakeRepository()
    gateway = FakeGateway()
    runner = GeminiAdvisoryReviewRunner(gateway=gateway)
    context = _context(repository, PrivacyMode.RESTRICTED)

    advisory = runner.ensure_advisory(repository, context.job)

    assert advisory["status"] == "READY"
    assert advisory["verdict"] == "PASS"
    assert len(gateway.calls) == 1
    assert repository.submitted_calls == 0


def test_private_review_never_calls_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    repository = FakeRepository()
    gateway = FakeGateway()
    runner = GeminiAdvisoryReviewRunner(gateway=gateway)

    result = runner.run(_context(repository, PrivacyMode.PRIVATE))

    assert result.status is StageResultStatus.BLOCKED
    assert result.error == "REVIEW_RESULT_PENDING"
    assert gateway.calls == []
    stored = load_gemini_recommendation(repository, "review-1")
    assert stored["status"] == "SKIPPED_PRIVATE"
