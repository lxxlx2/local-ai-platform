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
    StageContext,
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

    def capture_baseline(self):
        return self.identity.base_commit_sha


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

    def run_task(self, spec, execution_id):
        self.started_execution_id = execution_id
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
    unit = repo.create_work_unit(
        job.job_id, job.owner_id, WorkflowStage.PRODUCER,
        CodexTaskSpec(AI_ROOT, (CONTROL_PLANE_ROOT,), "safe producer", "LOW", 60, "CODE", {}),
    )
    run_id, _, idempotency_key = repo.begin_stage(job)
    execution_id = str(uuid.uuid4())
    repo.start_execution(execution_id, job.job_id, unit.work_unit_id, WorkflowStage.PRODUCER,
                         idempotency_key, "TestProvider")
    result = StageResult.passed("pass")
    repo.complete_execution(execution_id, result)
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


def mutating_work_unit(repo, job, stage=WorkflowStage.PRODUCER):
    return repo.create_work_unit(
        job.job_id, job.owner_id, stage,
        CodexTaskSpec(AI_ROOT, (CONTROL_PLANE_ROOT,), "bounded mutation", "LOW", 60, "CODE", {}),
        review_round=job.review_round,
    )


def test_durable_mutation_fence_survives_restart_and_requires_manual_reconciliation(tmp_path):
    provider = StaticIdentityProvider()
    first = repository(tmp_path, provider)
    blocked_job = stage_job(first, WorkflowStage.PRODUCER)
    unit = mutating_work_unit(first, blocked_job)
    run_id, _, key = first.begin_stage(blocked_job)
    execution_id = str(uuid.uuid4())
    first.start_execution(execution_id, blocked_job.job_id, unit.work_unit_id,
                          WorkflowStage.PRODUCER, key, "BoundProvider")
    fence = first.persist_mutation_fence(
        blocked_job.job_id, "LEASE_LOST_CANCELLATION_UNSUPPORTED", unit.work_unit_id, execution_id,
    )
    assert first.persist_mutation_fence(
        blocked_job.job_id, "LEASE_LOST_CANCELLATION_UNSUPPORTED", unit.work_unit_id, execution_id,
    )["fence_name"] == fence["fence_name"]
    first.update_job(blocked_job.job_id, status=JobStatus.BLOCKED,
                     resume_state="BLOCKED_REQUIRES_RECONCILIATION")
    first.close()

    reopened = repository(tmp_path, provider)
    read_only = reopened.create_job("read only", "owner", job_id="job-validation", mutation_capable=False)
    read_only = reopened.update_job(read_only.job_id, current_stage=WorkflowStage.VALIDATION)
    read_run, _, _ = reopened.begin_stage(read_only)
    assert read_run
    reopened.finish_stage(read_run, read_only.job_id, WorkflowStage.VALIDATION, StageResult.passed("read only"))
    reopened.update_job(read_only.job_id, status=JobStatus.BLOCKED)
    with pytest.raises(RuntimeError, match="MAX_MUTATING_JOBS_IN_SYSTEM"):
        reopened.create_job("other mutation", "owner", job_id="other-mutation")
    persisted = reopened.active_mutation_fence()
    assert persisted and persisted["requires_manual_reconciliation"] == 1
    assert "secret" not in json.dumps(persisted).lower()
    reopened.reconcile_mutation_fence(persisted["fence_name"], "operator verified external process ended")
    assert not reopened.has_active_mutation_fence()
    reopened.update_job(blocked_job.job_id, status=JobStatus.FAILED)
    other = reopened.create_job("other mutation", "owner", job_id="other-mutation")
    other = reopened.update_job(other.job_id, current_stage=WorkflowStage.PRODUCER)
    assert reopened.begin_stage(other) is not None
    reopened.close()


def test_lease_loss_with_unsupported_cancel_persists_global_fence(tmp_path):
    repo = repository(tmp_path)
    job = stage_job(repo, WorkflowStage.PRODUCER)
    context = StageContext(job, WorkflowStage.PRODUCER, 1, "lease-test", 60, repo)

    class SlowUnsupported:
        cancellation_supported = False

        def run(self, stage_context):
            time.sleep(0.03)
            return StageResult.passed("late")

    repo.heartbeat_external = lambda path, token, ttl: False
    wrapped = LeaseKeepingRunner(SlowUnsupported(), repo, "lease", 0.01, heartbeat_interval=0.005)
    with pytest.raises(LeaseLostError, match="durable fence persisted"):
        wrapped.run(context)
    assert repo.has_active_mutation_fence()
    repo.close()


class ExecutionBindingProvider:
    cancellation_supported = True

    def __init__(self):
        self.started = []
        self.canceled = []
        self.stage_runner = None

    def run_task(self, spec, execution_id):
        self.started.append(execution_id)
        assert CONTROL_PLANE_ROOT not in spec.allowed_paths
        assert all(path.is_file() for path in spec.allowed_paths)
        assert self.stage_runner.cancel(execution_id, "TEST_CANCEL")
        return StageResult.passed("bound execution completed")

    def cancel(self, execution_id=None, reason=None):
        self.canceled.append((execution_id, reason))
        return True


def run_bound_provider(repo, job):
    unit = mutating_work_unit(repo, job)
    run_id, attempt, key = repo.begin_stage(job)
    context = StageContext(repo.get_job(job.job_id), WorkflowStage.PRODUCER, attempt, key, 60, repo)
    provider = ExecutionBindingProvider()
    runner = PersistedCodexStageRunner(provider)
    provider.stage_runner = runner
    result = runner.run(context)
    repo.finish_stage(run_id, job.job_id, WorkflowStage.PRODUCER, result)
    repo.update_job(job.job_id, status=JobStatus.BLOCKED)
    execution = repo.db.execute(
        "SELECT * FROM supervisor_executions WHERE run_id=?", (run_id,),
    ).fetchone()
    return unit, provider, result, dict(execution)


def test_execution_id_is_bound_at_start_and_cancel_and_not_reused(tmp_path):
    repo = repository(tmp_path)
    first = stage_job(repo, WorkflowStage.PRODUCER)
    _, provider_one, result_one, execution_one = run_bound_provider(repo, first)
    assert result_one.status is StageResultStatus.PASS
    assert provider_one.started == [execution_one["execution_id"]]
    assert provider_one.canceled == [(execution_one["execution_id"], "TEST_CANCEL")]
    assert execution_one["cancellation_status"] == "CANCELED"

    repo.update_job(first.job_id, status=JobStatus.FAILED)
    second = repo.create_job("second", "owner", job_id="second")
    second = repo.update_job(second.job_id, current_stage=WorkflowStage.PRODUCER)
    _, provider_two, _, execution_two = run_bound_provider(repo, second)
    assert execution_two["execution_id"] != execution_one["execution_id"]
    assert provider_two.canceled[0][0] == execution_two["execution_id"]
    assert "secret" not in json.dumps(execution_two).lower()
    repo.close()


def test_execution_cancel_mismatch_is_rejected_without_provider_call():
    provider = CancellableTaskRunner(); runner = PersistedCodexStageRunner(provider)
    runner.execution_id = str(uuid.uuid4())
    assert not runner.cancel(str(uuid.uuid4()), "LEASE_LOST")
    assert provider.calls == [] and runner.cancellation_result == "EXECUTION_ID_MISMATCH"


def test_forged_completion_metrics_do_not_authorize_mutating_recovery(tmp_path):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.PRODUCER)
    run_id, _, _ = repo.begin_stage(job)
    forged = StageResult.passed("pass", metrics={
        "completion_provenance_confirmed": True, "execution_id": str(uuid.uuid4()),
    })
    repo.finish_stage(run_id, job.job_id, WorkflowStage.PRODUCER, forged)
    assert supervisor_for(repo).recover_interrupted() == 1
    assert repo.get_job(job.job_id).resume_state == "BLOCKED_REQUIRES_RECONCILIATION"
    repo.close()


def test_execution_record_for_other_job_cannot_authorize_recovery(tmp_path):
    repo = repository(tmp_path)
    other = stage_job(repo, WorkflowStage.PRODUCER)
    other_unit = mutating_work_unit(repo, other)
    other_run, _, other_key = repo.begin_stage(other)
    execution_id = str(uuid.uuid4())
    repo.start_execution(execution_id, other.job_id, other_unit.work_unit_id,
                         WorkflowStage.PRODUCER, other_key, "Provider")
    repo.complete_execution(execution_id, StageResult.passed("done"))
    repo.finish_stage(other_run, other.job_id, WorkflowStage.PRODUCER, StageResult.passed("done"))
    repo.update_job(other.job_id, status=JobStatus.COMPLETED)

    target = repo.create_job("target", "owner", job_id="target")
    target = repo.update_job(target.job_id, current_stage=WorkflowStage.PRODUCER)
    target_run, _, _ = repo.begin_stage(target)
    repo.finish_stage(target_run, target.job_id, WorkflowStage.PRODUCER, StageResult.passed("forged"))
    assert supervisor_for(repo).recover_interrupted() == 1
    assert repo.get_job(target.job_id).current_stage is WorkflowStage.PRODUCER
    repo.close()


def test_unresolved_fence_blocks_matching_confirmed_recovery(tmp_path):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.PRODUCER)
    unit = mutating_work_unit(repo, job)
    run_id, _, key = repo.begin_stage(job)
    execution_id = str(uuid.uuid4())
    repo.start_execution(execution_id, job.job_id, unit.work_unit_id,
                         WorkflowStage.PRODUCER, key, "Provider")
    result = StageResult.passed("done")
    repo.complete_execution(execution_id, result)
    repo.persist_mutation_fence(job.job_id, "EXTERNAL_EXECUTION_UNCERTAIN", unit.work_unit_id, execution_id)
    repo.finish_stage(run_id, job.job_id, WorkflowStage.PRODUCER, result)
    assert supervisor_for(repo).recover_interrupted() == 1
    assert repo.get_job(job.job_id).current_stage is WorkflowStage.PRODUCER
    repo.close()


def test_fence_created_after_stage_begin_still_denies_execution_start(tmp_path):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.PRODUCER)
    unit = mutating_work_unit(repo, job)
    _, _, key = repo.begin_stage(job)
    repo.persist_mutation_fence(job.job_id, "EXTERNAL_EXECUTION_UNCERTAIN", unit.work_unit_id)
    with pytest.raises(ValueError, match="fence"):
        repo.start_execution(str(uuid.uuid4()), job.job_id, unit.work_unit_id,
                             WorkflowStage.PRODUCER, key, "Provider")
    repo.close()


def test_safe_tracked_manifest_denies_sensitive_and_symlink_descendants(tmp_path):
    root = tmp_path / "repo"; allowed = root / "control-plane"; allowed.mkdir(parents=True); (root / "docs").mkdir()
    git(root, "init", "-b", "main"); git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    source = allowed / "source.py"; source.write_text("value = 1\n")
    env_file = allowed / ".env"; env_file.write_text("synthetic=true\n")
    db_file = allowed / "private.db"; db_file.write_text("not-a-real-db\n")
    pem_file = allowed / "secret.pem"; pem_file.write_text("synthetic\n")
    outside = root / "outside.py"; outside.write_text("outside = True\n")
    link = allowed / "linked.py"; link.symlink_to(outside)
    (root / ".gitignore").write_text("control-plane/ignored.secret\n")
    ignored = allowed / "ignored.secret"; ignored.write_text("synthetic\n")
    git(root, "add", ".gitignore", "control-plane")
    git(root, "commit", "-m", "fixture")
    policy = RepoAccessPolicy(root)
    manifest = policy.build_safe_file_manifest((allowed,))
    paths = {item["path"] for item in manifest}
    assert paths == {"control-plane/source.py"}
    assert policy.read_safe_file("control-plane/source.py", (allowed,), manifest) == b"value = 1\n"
    for denied in ("control-plane/.env", "control-plane/private.db", "control-plane/secret.pem",
                   "control-plane/ignored.secret", "control-plane/linked.py"):
        with pytest.raises(PermissionError):
            policy.read_safe_file(denied, (allowed,), manifest)


def test_trusted_baseline_is_captured_immutable_and_survives_restart(tmp_path):
    provider = StaticIdentityProvider()
    repo = repository(tmp_path, provider)
    job = repo.create_job("baseline", "owner", job_id="baseline")
    assert job.baseline_commit_sha == provider.identity.base_commit_sha
    with pytest.raises(ValueError, match="baseline"):
        repo.update_job_metadata(job.job_id, {"candidate_base_commit_sha": "f" * 40})
    with pytest.raises(ValueError, match="baseline"):
        repo.create_job("bad", "owner", metadata={"baseline_commit_sha": "f" * 40})
    repo.close()
    reopened = repository(tmp_path, provider)
    assert reopened.get_job(job.job_id).baseline_commit_sha == provider.identity.base_commit_sha
    reopened.close()


def test_candidate_provider_rejects_invalid_and_unrelated_baselines(tmp_path):
    root, base = identity_repo(tmp_path)
    provider = CandidateIdentityProvider(root)
    assert provider.validate_baseline(base) == base
    with pytest.raises((ValueError, RuntimeError)):
        provider.validate_baseline("f" * 40)
    git(root, "checkout", "--orphan", "unrelated")
    (root / "control-plane" / "worker.py").write_text("unrelated = True\n")
    git(root, "add", "control-plane/worker.py"); git(root, "commit", "-m", "unrelated")
    unrelated = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "main")
    with pytest.raises(RuntimeError):
        provider.validate_baseline(unrelated)


def test_candidate_created_at_is_integrity_bound_but_not_candidate_identity(tmp_path):
    repo = repository(tmp_path); prepare_review(repo); unit = create_unit(repo)
    same = CandidateIdentity.from_mapping(unit.candidate_identity.to_mapping() | {
        "candidate_created_at": "2026-08-23T00:00:00+00:00",
    })
    assert unit.candidate_identity.same_candidate(same)
    payload = unit.candidate_identity.to_mapping() | {"candidate_created_at": "2027-01-01T00:00:00+00:00"}
    with repo.db:
        repo.db.execute(
            "UPDATE supervisor_review_work_units SET candidate_identity_json=? WHERE review_work_unit_id=?",
            (json.dumps(payload), unit.review_work_unit_id),
        )
    with pytest.raises(ValueError, match="integrity"):
        repo.get_review_work_unit(unit.review_work_unit_id, "job", "owner", 1)
    repo.close()


def review_transition_fixture(repo, result):
    job = prepare_review(repo)
    unit = create_unit(repo)
    repo.submit_review_result(job.job_id, job.owner_id, 1, unit.review_work_unit_id, result)
    run_id, _, _ = repo.begin_stage(job)
    repo.finish_stage(run_id, job.job_id, WorkflowStage.REVIEW, result.to_stage_result())
    return job, unit


def test_submitted_review_result_reconciles_only_after_matching_transition(tmp_path):
    provider = StaticIdentityProvider(); repo = repository(tmp_path, provider)
    job, unit = review_transition_fixture(repo, ReviewResult("PASS"))
    repo.update_job(job.job_id, status=JobStatus.QUEUED, current_stage=WorkflowStage.SECURITY)
    repo.close()
    reopened = repository(tmp_path, provider)
    assert reopened.reconcile_submitted_review_results() == 1
    assert reopened.reconcile_submitted_review_results() == 0
    status = reopened.db.execute(
        "SELECT status FROM supervisor_review_results WHERE review_work_unit_id=?", (unit.review_work_unit_id,),
    ).fetchone()[0]
    assert status == "CONSUMED"
    reopened.close()


def test_stale_review_result_is_not_auto_consumed(tmp_path):
    repo = repository(tmp_path); job, unit = review_transition_fixture(repo, ReviewResult("PASS"))
    repo.update_job(job.job_id, status=JobStatus.QUEUED, current_stage=WorkflowStage.REVISION)
    assert repo.reconcile_submitted_review_results() == 0
    status = repo.db.execute(
        "SELECT status FROM supervisor_review_results WHERE review_work_unit_id=?", (unit.review_work_unit_id,),
    ).fetchone()[0]
    assert status == "SUBMITTED"
    repo.close()


def test_round5_unresolved_execution_becomes_idempotent_global_fence(tmp_path):
    provider = StaticIdentityProvider(); repo = repository(tmp_path, provider)
    job = stage_job(repo, WorkflowStage.PRODUCER); unit = mutating_work_unit(repo, job)
    _, _, key = repo.begin_stage(job)
    execution_id = str(uuid.uuid4())
    repo.start_execution(execution_id, job.job_id, unit.work_unit_id,
                         WorkflowStage.PRODUCER, key, "Provider")
    assert repo.ensure_unresolved_execution_fences() == 1
    assert repo.ensure_unresolved_execution_fences() == 0
    assert repo.has_mutation_guard()
    fence = repo.active_mutation_fence()
    repo.reconcile_mutation_fence(fence["fence_name"], "operator verified process termination")
    assert not repo.has_mutation_guard()
    status = repo.db.execute(
        "SELECT completion_status FROM supervisor_executions WHERE execution_id=?", (execution_id,),
    ).fetchone()[0]
    assert status == "MANUALLY_RECONCILED"
    repo.close()


def test_round5_cancellation_and_completion_cannot_form_confirmed_canceled_state(tmp_path):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.PRODUCER)
    unit = mutating_work_unit(repo, job); _, _, key = repo.begin_stage(job)
    execution_id = str(uuid.uuid4())
    repo.start_execution(execution_id, job.job_id, unit.work_unit_id,
                         WorkflowStage.PRODUCER, key, "Provider")
    repo.record_execution_cancellation(execution_id, "CANCELED")
    row = repo.complete_execution(execution_id, StageResult.passed("late pass"))
    assert row["completion_status"] == "COMPLETED_NONPASS"
    assert repo.confirmed_execution_for_run(row["run_id"], job.job_id, WorkflowStage.PRODUCER) is None
    repo.close()


def test_round5_confirmed_execution_is_bound_to_post_execution_candidate(tmp_path):
    provider = StaticIdentityProvider(); repo = repository(tmp_path, provider)
    job = stage_job(repo, WorkflowStage.PRODUCER); unit = mutating_work_unit(repo, job)
    _, _, key = repo.begin_stage(job); execution_id = str(uuid.uuid4())
    row = repo.start_execution(execution_id, job.job_id, unit.work_unit_id,
                               WorkflowStage.PRODUCER, key, "Provider")
    completed = repo.complete_execution(execution_id, StageResult.passed("done"))
    assert repo.confirmed_execution_for_run(row["run_id"], job.job_id, WorkflowStage.PRODUCER)
    provider.identity = candidate_identity(paths=(CANDIDATE_FILE,))
    assert repo.confirmed_execution_for_run(row["run_id"], job.job_id, WorkflowStage.PRODUCER) is None
    assert completed["candidate_state_sha256"]
    repo.close()


def test_round5_only_one_nonterminal_mutation_capable_job(tmp_path):
    repo = repository(tmp_path)
    first = repo.create_job("first", "owner", job_id="first")
    with pytest.raises(RuntimeError, match="MAX_MUTATING_JOBS_IN_SYSTEM=1"):
        repo.create_job("second", "owner", job_id="second")
    read_only = repo.create_job("status probe", "owner", job_id="status", mutation_capable=False)
    assert read_only.mutation_capable is False
    repo.update_job(first.job_id, status=JobStatus.COMPLETED)
    assert repo.create_job("second", "owner", job_id="second").mutation_capable is True
    repo.close()


def test_round5_candidate_manifest_includes_safe_untracked_and_fails_closed(tmp_path):
    root = tmp_path / "repo"; allowed = root / "control-plane"; allowed.mkdir(parents=True); (root / "docs").mkdir()
    git(root, "init", "-b", "main"); git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    tracked = allowed / "tracked.py"; tracked.write_text("value = 1\n")
    git(root, "add", "."); git(root, "commit", "-m", "base")
    new_file = allowed / "new.py"; new_file.write_text("new_value = 2\n")
    policy = RepoAccessPolicy(root)
    identity = CandidateIdentityProvider(root, policy).snapshot(git(root, "rev-parse", "HEAD"))
    manifest = policy.build_candidate_file_manifest(identity, (allowed,))
    assert {item["path"] for item in manifest} == {"control-plane/new.py"}
    new_file.write_text('token = "hf_' + 'a' * 32 + '"\n')
    secret_identity = CandidateIdentityProvider(root, policy).snapshot(git(root, "rev-parse", "HEAD"))
    with pytest.raises(ValueError, match="Secret Firewall"):
        policy.build_candidate_file_manifest(secret_identity, (allowed,))


def test_round5_tracked_ordinary_text_manifest_fails_closed_on_secret(tmp_path):
    root = tmp_path / "repo"; allowed = root / "control-plane"; allowed.mkdir(parents=True); (root / "docs").mkdir()
    git(root, "init", "-b", "main"); git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (allowed / "settings.py").write_text('credential = "hf_' + 'a' * 32 + '"\n')
    git(root, "add", "."); git(root, "commit", "-m", "base")
    with pytest.raises(ValueError, match="Secret Firewall"):
        RepoAccessPolicy(root).build_safe_file_manifest((allowed,))


def test_round5_candidate_manifest_denies_binary_and_symlink(tmp_path):
    root = tmp_path / "repo"; allowed = root / "control-plane"; allowed.mkdir(parents=True); (root / "docs").mkdir()
    git(root, "init", "-b", "main"); git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Fixture")
    (allowed / "base.py").write_text("x = 1\n"); git(root, "add", "."); git(root, "commit", "-m", "base")
    binary = allowed / "new.bin"; binary.write_bytes(b"x\0y")
    policy = RepoAccessPolicy(root)
    identity = CandidateIdentityProvider(root, policy).snapshot(git(root, "rev-parse", "HEAD"))
    with pytest.raises(ValueError, match="binary"):
        policy.build_candidate_file_manifest(identity, (allowed,))
    binary.unlink(); outside = root / "outside.py"; outside.write_text("x=2\n")
    (allowed / "link.py").symlink_to(outside)
    with pytest.raises(PermissionError):
        CandidateIdentityProvider(root, policy).snapshot(git(root, "rev-parse", "HEAD"))


def test_round5_snapshot_failure_never_confirms_execution(tmp_path):
    provider = StaticIdentityProvider(); repo = repository(tmp_path, provider)
    job = stage_job(repo, WorkflowStage.PRODUCER); unit = mutating_work_unit(repo, job)
    _, _, key = repo.begin_stage(job); execution_id = str(uuid.uuid4())
    repo.start_execution(execution_id, job.job_id, unit.work_unit_id,
                         WorkflowStage.PRODUCER, key, "Provider")
    provider.snapshot = lambda baseline=None: (_ for _ in ()).throw(RuntimeError("probe unavailable"))
    row = repo.complete_execution(execution_id, StageResult.passed("done"))
    assert row["completion_status"] == "UNKNOWN"
    assert repo.has_mutation_guard()
    repo.close()


def test_round5_first_producer_rejects_worktree_ownership_change(tmp_path):
    provider = StaticIdentityProvider(); repo = repository(tmp_path, provider)
    job = repo.create_job("owned", "owner", job_id="owned")
    job = repo.update_job(job.job_id, current_stage=WorkflowStage.PRODUCER)
    provider.identity = candidate_identity(paths=(CANDIDATE_FILE,))
    assert repo.begin_stage(job) is None
    repo.close()


def test_round5_confirmed_execution_rejects_late_cancellation(tmp_path):
    repo = repository(tmp_path); job = stage_job(repo, WorkflowStage.PRODUCER)
    unit = mutating_work_unit(repo, job); _, _, key = repo.begin_stage(job)
    execution_id = str(uuid.uuid4())
    repo.start_execution(execution_id, job.job_id, unit.work_unit_id,
                         WorkflowStage.PRODUCER, key, "Provider")
    row = repo.complete_execution(execution_id, StageResult.passed("done"))
    assert row["completion_status"] == "COMPLETED_CONFIRMED"
    with pytest.raises(ValueError, match="cannot be canceled"):
        repo.record_execution_cancellation(execution_id, "CANCELED")
    current = repo.db.execute(
        "SELECT completion_status,cancellation_status FROM supervisor_executions WHERE execution_id=?",
        (execution_id,),
    ).fetchone()
    assert tuple(current) == ("COMPLETED_CONFIRMED", "NOT_REQUESTED")
    repo.close()
