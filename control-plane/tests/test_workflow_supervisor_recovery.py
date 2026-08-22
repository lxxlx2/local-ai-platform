import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from local_ai_control.services.supervisor import (
    AI_ROOT,
    CONTROL_PLANE_ROOT,
    CandidateIdentity,
    CandidateIdentityProvider,
    CodexTaskSpec,
    JobStatus,
    LeaseKeepingRunner,
    LeaseLostError,
    PersistedCodexStageRunner,
    RealCodexRunner,
    RepoAccessPolicy,
    ReviewFinding,
    ReviewResult,
    ReviewTaskSpec,
    StageResult,
    StageResultStatus,
    StaticPassRunner,
    SupervisorRepository,
    WorkflowStage,
    WorkflowSupervisor,
)
import local_ai_control.services.supervisor_round2 as round2


CANDIDATE_FILE = "control-plane/src/local_ai_control/services/supervisor_contracts.py"
SECOND_CANDIDATE_FILE = "control-plane/src/local_ai_control/services/supervisor_round2_review.py"


def candidate_identity(paths=(CANDIDATE_FILE, SECOND_CANDIDATE_FILE), deleted=()):
    return CandidateIdentity(
        "TREE_MANIFEST", None, "b" * 64, "a" * 40, "c" * 64,
        "2026-08-22T00:00:00+00:00", tuple(paths), tuple(deleted),
    )


class StaticIdentityProvider:
    def __init__(self, identity=None):
        self.identity = identity or candidate_identity()

    def snapshot(self, base_commit_sha=None):
        return self.identity


def repository(tmp_path, provider=None):
    value = SupervisorRepository(
        tmp_path / "supervisor.db",
        candidate_identity_provider=provider or StaticIdentityProvider(),
    )
    value.migrate()
    return value


def review_spec(allowed=(CONTROL_PLANE_ROOT,)):
    return ReviewTaskSpec(
        AI_ROOT, tuple(allowed), "bounded independent review", True, "LOW", 60,
        "REVIEW", round2.REVIEW_RESULT_SCHEMA,
    )


def prepare_review(repo, job_id="job", review_round=0, max_review_rounds=3):
    job = repo.create_job("review", "owner", job_id=job_id, max_review_rounds=max_review_rounds)
    return repo.update_job(job.job_id, current_stage=WorkflowStage.REVIEW, review_round=review_round)


def create_unit(repo, job_id="job", review_round=1, identifier="review-unit", allowed=(CONTROL_PLANE_ROOT,)):
    return repo.create_review_work_unit(
        job_id, "owner", review_round, review_spec(allowed), identifier,
    )


def test_future_review_round_is_rejected(tmp_path):
    repo = repository(tmp_path); prepare_review(repo)
    with pytest.raises(ValueError):
        create_unit(repo, review_round=2)
    repo.close()


def test_non_review_stage_cannot_create_reviewer_work_unit(tmp_path):
    repo = repository(tmp_path); repo.create_job("job", "owner", job_id="job")
    with pytest.raises(ValueError, match="REVIEW stage"):
        create_unit(repo)
    repo.close()


def test_candidate_identity_persists_and_reopens_exactly(tmp_path):
    provider = StaticIdentityProvider()
    repo = repository(tmp_path, provider); prepare_review(repo)
    unit = create_unit(repo); repo.close()
    reopened = repository(tmp_path, provider)
    assert reopened.get_review_work_unit(unit.review_work_unit_id, "job", "owner", 1).candidate_identity == unit.candidate_identity
    reopened.close()


def test_candidate_change_rejects_stale_review_submission(tmp_path):
    provider = StaticIdentityProvider()
    repo = repository(tmp_path, provider); prepare_review(repo); unit = create_unit(repo)
    provider.identity = candidate_identity(paths=(CANDIDATE_FILE,))
    with pytest.raises(ValueError, match="stale"):
        repo.submit_review_result("job", "owner", 1, unit.review_work_unit_id, ReviewResult("PASS"))
    repo.close()


def test_stale_pass_cannot_cross_revision_lifecycle(tmp_path):
    repo = repository(tmp_path); prepare_review(repo); unit = create_unit(repo)
    repo.update_job("job", current_stage=WorkflowStage.REVISION, review_round=1)
    with pytest.raises(ValueError, match="lifecycle"):
        repo.submit_review_result("job", "owner", 1, unit.review_work_unit_id, ReviewResult("PASS"))
    repo.close()


def test_same_unit_same_identity_submission_is_idempotent(tmp_path):
    repo = repository(tmp_path); prepare_review(repo); unit = create_unit(repo)
    first = repo.submit_review_result("job", "owner", 1, unit.review_work_unit_id, ReviewResult("PASS"))
    second = repo.submit_review_result("job", "owner", 1, unit.review_work_unit_id, ReviewResult("PASS"))
    assert first.result_hash == second.result_hash
    repo.close()


def test_same_unit_conflicting_candidate_identity_is_rejected(tmp_path):
    provider = StaticIdentityProvider()
    repo = repository(tmp_path, provider); prepare_review(repo); create_unit(repo)
    provider.identity = candidate_identity(paths=(CANDIDATE_FILE,))
    with pytest.raises(ValueError, match="immutable manifest"):
        create_unit(repo)
    repo.close()


def git(root, *args):
    return subprocess.run(("git", *args), cwd=root, capture_output=True, text=True, shell=False,
                          timeout=10, check=True).stdout.strip()


def identity_repo(tmp_path):
    root = tmp_path / "repo"; (root / "control-plane").mkdir(parents=True); (root / "docs").mkdir()
    git(root, "init", "-b", "main"); git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    source = root / "control-plane" / "worker.py"; source.write_text("value = 1\n")
    git(root, "add", "control-plane/worker.py"); git(root, "commit", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    source.write_text("value = 2\n")
    return root, base


def test_candidate_identity_probe_is_read_only_and_deterministic(tmp_path):
    root, base = identity_repo(tmp_path)
    before = git(root, "status", "--porcelain=v1")
    provider = CandidateIdentityProvider(root)
    first = provider.snapshot(base); second = provider.snapshot(base)
    after = git(root, "status", "--porcelain=v1")
    assert first.same_candidate(second) and before == after


def test_candidate_identity_contains_hashes_not_secret_body(tmp_path):
    root, base = identity_repo(tmp_path)
    body = "pass" + "word=synthetic-private-value"
    (root / "control-plane" / "worker.py").write_text(body)
    identity = CandidateIdentityProvider(root).snapshot(base)
    assert body not in json.dumps(identity.to_mapping()) and identity.candidate_paths == ("control-plane/worker.py",)


def test_agent_policy_rejects_repo_root_blanket():
    with pytest.raises(PermissionError):
        RepoAccessPolicy().validate_allowed_paths([AI_ROOT])


def test_agent_policy_allows_control_plane_and_docs():
    result = RepoAccessPolicy().validate_allowed_paths([CONTROL_PLANE_ROOT, AI_ROOT / "docs"])
    assert result == (CONTROL_PLANE_ROOT.resolve(), (AI_ROOT / "docs").resolve())


@pytest.mark.parametrize("relative", [
    "runtime", "runtime/secrets", ".env", "private.db", "models", "cache", "logs",
])
def test_agent_policy_rejects_runtime_secret_model_and_database_paths(relative):
    with pytest.raises(PermissionError):
        RepoAccessPolicy().validate_candidate_path(relative, (CONTROL_PLANE_ROOT,), deleted=True)


def test_agent_policy_rejects_symlink_escape(tmp_path):
    root = tmp_path / "repo"; allowed = root / "control-plane"; runtime = root / "runtime"
    allowed.mkdir(parents=True); runtime.mkdir(); (allowed / "link").symlink_to(runtime, target_is_directory=True)
    policy = RepoAccessPolicy(root)
    with pytest.raises(PermissionError):
        policy.validate_candidate_path("control-plane/link/secret.txt", (allowed,), deleted=True)


def test_agent_policy_rejects_symlink_component_even_when_target_stays_allowed(tmp_path):
    root = tmp_path / "repo"; allowed = root / "control-plane"; target = allowed / "target"
    target.mkdir(parents=True); (target / "source.py").write_text("value = 1\n")
    (allowed / "alias").symlink_to(target, target_is_directory=True)
    policy = RepoAccessPolicy(root)
    with pytest.raises(PermissionError, match="symlink"):
        policy.validate_candidate_path("control-plane/alias/source.py", (allowed,))


def test_producer_and_revision_specs_reject_runtime_scope():
    with pytest.raises(PermissionError):
        CodexTaskSpec(AI_ROOT, (AI_ROOT / "runtime",), "safe", "LOW", 60, "CODE", {}).validate()


class CancellableTaskRunner:
    cancellation_supported = True

    def __init__(self, raises=False):
        self.calls = []; self.raises = raises

    def run_task(self, spec):
        return StageResult.passed("done")

    def cancel(self, execution_id=None, reason=None):
        self.calls.append((execution_id, reason))
        if self.raises:
            raise RuntimeError("cancel failure")
        return True


def test_persisted_codex_cancel_reaches_internal_runner_with_safe_id():
    inner = CancellableTaskRunner(); runner = PersistedCodexStageRunner(inner)
    runner.execution_id = str(uuid.uuid4())
    assert runner.cancel(reason="LEASE_LOST")
    assert inner.calls == [(runner.execution_id, "LEASE_LOST")]
    assert "secret" not in inner.calls[0][0].lower()


def test_persisted_codex_cancel_exception_fails_safe():
    runner = PersistedCodexStageRunner(CancellableTaskRunner(raises=True))
    runner.execution_id = str(uuid.uuid4())
    assert not runner.cancel(reason="LEASE_LOST") and runner.cancellation_result == "CANCEL_FAILED"


def test_real_codex_runner_cancellation_and_execution_remain_disabled():
    runner = RealCodexRunner()
    assert runner.cancellation_supported is False and runner.cancel(reason="LEASE_LOST") is False


def test_unsupported_lease_cancel_requires_reconciliation(tmp_path):
    class Repo:
        path = tmp_path / "unused.db"
        lease_failed = False
        external_execution_may_still_be_active = False

        @staticmethod
        def heartbeat_external(path, token, ttl):
            return False

    class Slow:
        cancellation_supported = False

        def run(self, context):
            time.sleep(0.03)
            return StageResult.passed("late")

    wrapped = LeaseKeepingRunner(Slow(), Repo(), "lease", 0.01, heartbeat_interval=0.005)
    with pytest.raises(LeaseLostError, match="EXTERNAL_EXECUTION_MAY_STILL_BE_ACTIVE"):
        wrapped.run(None)
    assert wrapped.external_execution_may_still_be_active


def test_metadata_patch_recursively_sanitizes_and_preserves_existing(tmp_path):
    repo = repository(tmp_path); repo.create_job("meta", "owner", job_id="meta", metadata={"keep": "yes"})
    raw_prompt = "private prompt body"; raw_auth = "private auth body"
    updated = repo.update_job_metadata("meta", {"nested": {"prompt": raw_prompt}, "items": [{"authorization": raw_auth}]})
    stored = repo.db.execute("SELECT metadata_json FROM supervisor_jobs WHERE job_id='meta'").fetchone()[0]
    assert updated.metadata["keep"] == "yes" and raw_prompt not in stored and raw_auth not in stored
    assert "prompt_sha256" in stored and "authorization_sha256" in stored
    second = repo.update_job_metadata("meta", {"normal": "readable"})
    assert "prompt_sha256" in second.metadata["nested"] and "prompt_sha256_sha256" not in stored
    repo.close()


@pytest.mark.parametrize("value", ["{}", 3, ["not", "mapping"]])
def test_metadata_patch_rejects_serialized_or_invalid_types(tmp_path, value):
    repo = repository(tmp_path); repo.create_job("meta", "owner", job_id="meta")
    with pytest.raises(TypeError):
        repo.update_job_metadata("meta", value)
    repo.close()


def test_metadata_patch_rejects_non_json_and_oversized_values(tmp_path):
    repo = repository(tmp_path); repo.create_job("meta", "owner", job_id="meta")
    with pytest.raises(ValueError):
        repo.update_job_metadata("meta", {"bad": object()})
    with pytest.raises(ValueError, match="exceeds"):
        repo.update_job_metadata("meta", {"large": "x" * 20_000})
    repo.close()


def test_raw_metadata_json_update_is_denied(tmp_path):
    repo = repository(tmp_path); repo.create_job("meta", "owner", job_id="meta")
    with pytest.raises(ValueError):
        repo.update_job("meta", metadata_json='{"raw":"bypass"}')
    repo.close()


def submit_finding(tmp_path, identity, finding, allowed=(CONTROL_PLANE_ROOT,)):
    repo = repository(tmp_path, StaticIdentityProvider(identity)); prepare_review(repo)
    unit = create_unit(repo, allowed=allowed)
    try:
        return repo.submit_review_result("job", "owner", 1, unit.review_work_unit_id,
                                         ReviewResult("FAIL", (finding,)))
    finally:
        repo.close()


def test_review_finding_inside_allowed_candidate_source_is_accepted(tmp_path):
    result = submit_finding(tmp_path, candidate_identity(paths=(CANDIDATE_FILE,)),
                            ReviewFinding("HIGH", CANDIDATE_FILE, "e", "f"))
    assert result.status == "SUBMITTED"


def test_review_finding_inside_repo_but_outside_allowed_paths_is_rejected(tmp_path):
    identity = candidate_identity(paths=("docs/ARCHITECTURE.md",))
    with pytest.raises(PermissionError):
        submit_finding(tmp_path, identity, ReviewFinding("HIGH", "docs/ARCHITECTURE.md", "e", "f"))


@pytest.mark.parametrize("path", ["runtime/secret.txt", "../escape", "control-plane/not-candidate.py"])
def test_review_finding_runtime_traversal_and_non_candidate_are_rejected(tmp_path, path):
    with pytest.raises(PermissionError):
        submit_finding(tmp_path, candidate_identity(paths=(CANDIDATE_FILE,)),
                       ReviewFinding("HIGH", path, "e", "f"))


def test_deleted_candidate_path_finding_is_explicitly_accepted(tmp_path):
    deleted = "control-plane/deleted-source.py"
    identity = candidate_identity(paths=(deleted,), deleted=(deleted,))
    result = submit_finding(tmp_path, identity, ReviewFinding("MEDIUM", deleted, "e", "f"))
    assert result.status == "SUBMITTED"


def stage_job(repo, stage, max_attempts=1):
    job = repo.create_job("recovery", "owner", job_id=f"job-{stage.value.lower()}",
                          max_attempts_per_stage=max_attempts)
    return repo.update_job(job.job_id, current_stage=stage)


def supervisor_for(repo):
    runners = {stage: StaticPassRunner(stage.value) for stage in WorkflowStage if stage is not WorkflowStage.DONE}
    return WorkflowSupervisor(repo, runners)


def test_completed_read_only_stage_recovers_without_rerun_or_false_attempt_block(tmp_path):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.VALIDATION, max_attempts=1)
    run_id, _, _ = repo.begin_stage(job)
    repo.finish_stage(run_id, job.job_id, WorkflowStage.VALIDATION, StageResult.passed("durable pass"))
    supervisor = supervisor_for(repo)
    assert supervisor.recover_interrupted() == 1
    recovered = repo.get_job(job.job_id)
    assert recovered.current_stage is WorkflowStage.SELF_ACCEPTANCE and recovered.status is JobStatus.QUEUED
    assert repo.stage_attempts(job.job_id, WorkflowStage.VALIDATION) == 1
    assert supervisor.recover_interrupted() == 0
    repo.close()


def test_completed_mutating_stage_requires_confirmed_provenance(tmp_path):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.PRODUCER)
    run_id, _, _ = repo.begin_stage(job)
    repo.finish_stage(run_id, job.job_id, WorkflowStage.PRODUCER, StageResult.passed("pass"))
    assert supervisor_for(repo).recover_interrupted() == 1
    recovered = repo.get_job(job.job_id)
    assert recovered.status is JobStatus.BLOCKED and recovered.resume_state == "BLOCKED_REQUIRES_RECONCILIATION"
    repo.close()


def test_completed_mutating_stage_with_confirmed_provenance_finalizes(tmp_path):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.PRODUCER)
    run_id, _, _ = repo.begin_stage(job)
    result = StageResult.passed("pass", metrics={"completion_provenance_confirmed": True,
                                                  "execution_id": str(uuid.uuid4())})
    repo.finish_stage(run_id, job.job_id, WorkflowStage.PRODUCER, result)
    assert supervisor_for(repo).recover_interrupted() == 1
    assert repo.get_job(job.job_id).current_stage is WorkflowStage.VALIDATION
    repo.close()


@pytest.mark.parametrize("status", [StageResultStatus.FAIL, StageResultStatus.TIMEOUT])
def test_failed_or_timed_out_stage_is_not_misclassified_as_completed(tmp_path, status):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.VALIDATION, max_attempts=2)
    run_id, _, _ = repo.begin_stage(job)
    repo.finish_stage(run_id, job.job_id, WorkflowStage.VALIDATION,
                      StageResult(status, status.value, error=status.value))
    assert supervisor_for(repo).recover_interrupted() == 1
    assert repo.get_job(job.job_id).resume_state == "INTERRUPTED_SAFE_RETRY"
    latest = repo.latest_stage_runs(job.job_id)[-1]
    assert latest["status"] == status.value
    repo.close()
