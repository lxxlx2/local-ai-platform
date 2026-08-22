from __future__ import annotations

import os
import time
import uuid
from typing import Mapping

from .supervisor_contracts import (
    LOCK_TTL_SECONDS, NEXT_STAGE, SAFE_RECOVERY_STAGES, TERMINAL_JOB_STATUSES,
    JobStatus, LeaseLostError, StageContext, StageResult, StageResultStatus,
    StageRunner, WorkflowJob, WorkflowStage, _safe_text, utc_now,
)
from .supervisor_repository import SupervisorRepository


HARD_BLOCK_REASONS = {
    "BLOCKED_REQUIRES_RECONCILIATION",
    "MAX_REVIEW_ROUNDS",
    "GIT_GATE_BLOCKED",
    "SECURITY_BLOCKED",
    "RUNNER_NOT_CONFIGURED",
    "REVIEW_FINDINGS_MISSING",
    "TRANSITION_LIMIT",
}


class WorkflowSupervisor:
    def __init__(self, repository: SupervisorRepository, runners: Mapping[WorkflowStage, StageRunner],
                 timeout_seconds: float = 120, retry_backoff_seconds: float = 0):
        self.repository = repository
        self.runners = dict(runners)
        self.timeout_seconds = timeout_seconds
        self.retry_backoff_seconds = max(0, retry_backoff_seconds)
        self.lock_ttl_seconds = max(LOCK_TTL_SECONDS, int(timeout_seconds) + 60)
        self.lock_token = str(uuid.uuid4())
        self.locked = False

    def acquire_singleton(self, pid: int | None = None) -> bool:
        self.locked = self.repository.acquire_lock(
            self.lock_token, pid or os.getpid(), ttl=self.lock_ttl_seconds,
        )
        return self.locked

    def release_singleton(self) -> None:
        if self.locked:
            self.repository.release_lock(self.lock_token)
            self.locked = False

    def heartbeat(self) -> bool:
        return self.locked and self.repository.heartbeat_lock(self.lock_token, ttl=self.lock_ttl_seconds)

    def _require_lease(self) -> None:
        if not self.locked:
            raise RuntimeError("single-instance lock required")
        if not self.heartbeat():
            self.locked = False
            try:
                self.repository.record_event(None, "LEASE_LOST", payload={"action": "STOP_CONSUMING"})
            except Exception:
                pass
            raise LeaseLostError("singleton lease lost; consumer stopped")

    def create_demo(self, owner_id: str, job_id: str | None = None) -> WorkflowJob:
        return self.repository.create_job(
            "SUPERVISOR_DEMO", owner_id,
            metadata={"supervisor_demo": True, "allow_git_mutation": False},
            max_review_rounds=2, max_attempts_per_stage=2, job_id=job_id,
        )

    def status(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        return self.repository.get_job_for_owner(job_id, owner_id) if owner_id is not None else self.repository.get_job(job_id)

    def list_jobs(self, owner_id: str | None = None, limit=50) -> list[WorkflowJob]:
        return self.repository.list_jobs(owner_id, limit)

    def pause(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        job = self.status(job_id, owner_id)
        if job.status is JobStatus.BLOCKED or job.status in TERMINAL_JOB_STATUSES or job.resume_state == "PAUSED":
            return job
        state = "PAUSE_REQUESTED" if job.status is JobStatus.RUNNING else "PAUSED"
        status = JobStatus.RUNNING if job.status is JobStatus.RUNNING else JobStatus.WAITING
        job = self.repository.update_job(job_id, status=status, resume_state=state)
        self.repository.record_event(job_id, "JOB_PAUSED", job.current_stage, {"state": state}, f"pause:{job_id}:{state}")
        return job

    def resume(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        job = self.status(job_id, owner_id)
        if job.status is not JobStatus.WAITING or job.resume_state != "PAUSED":
            return job
        job = self.repository.update_job(job_id, status=JobStatus.QUEUED, resume_state=None, next_retry_at=None)
        self.repository.record_event(job_id, "JOB_RESUMED", job.current_stage, {},
                                     f"resume:{job_id}:{job.current_stage.value}:{job.attempt}")
        return job

    def cancel(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        job = self.status(job_id, owner_id)
        if job.status in {JobStatus.CANCELED, JobStatus.COMPLETED}:
            return job
        if job.status is JobStatus.RUNNING:
            job = self.repository.update_job(job_id, resume_state="CANCEL_REQUESTED")
        else:
            job = self.repository.update_job(job_id, status=JobStatus.CANCELED, resume_state=None)
        self.repository.record_event(job_id, "JOB_CANCELED", job.current_stage, {}, f"cancel:{job_id}")
        return job

    def retry(self, job_id: str, owner_id: str | None = None) -> WorkflowJob:
        job = self.status(job_id, owner_id)
        if job.status not in {JobStatus.FAILED, JobStatus.BLOCKED}:
            return job
        if job.status is JobStatus.BLOCKED and (job.resume_state in HARD_BLOCK_REASONS or job.current_stage is WorkflowStage.GIT_GATE):
            return job
        if self.repository.stage_attempts(job_id, job.current_stage) >= job.max_attempts_per_stage:
            return job
        job = self.repository.update_job(job_id, status=JobStatus.QUEUED, resume_state=None,
                                         last_error=None, next_retry_at=None)
        self.repository.record_event(job_id, "RETRY_SCHEDULED", job.current_stage,
                                     {"manual": True}, f"retry:{job_id}:{job.current_stage.value}:{job.attempt}")
        return job

    def recover_interrupted(self) -> int:
        recovered = 0
        self.repository.ensure_unresolved_execution_fences()
        if hasattr(self.repository, "reconcile_submitted_review_results"):
            self.repository.reconcile_submitted_review_results()
        for job in self.repository.list_jobs(limit=200):
            if job.status is not JobStatus.RUNNING:
                continue
            latest = self.repository.db.execute(
                "SELECT * FROM supervisor_stage_runs WHERE job_id=? AND stage=? "
                "ORDER BY attempt DESC,started_at DESC LIMIT 1",
                (job.job_id, job.current_stage.value),
            ).fetchone()
            if latest and latest["status"] == StageResultStatus.PASS.value and latest["completed_at"]:
                mutating = job.current_stage in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}
                confirmed = (not mutating or (
                    self.repository.confirmed_execution_for_run(
                        latest["run_id"], job.job_id, job.current_stage,
                    ) is not None
                    and not self.repository.has_active_mutation_fence()
                ))
                if mutating and not confirmed:
                    self.repository.update_job(
                        job.job_id, status=JobStatus.BLOCKED,
                        resume_state="BLOCKED_REQUIRES_RECONCILIATION",
                        last_error="COMPLETED_NOT_TRANSITIONED_UNCONFIRMED",
                    )
                    self.repository.record_event(
                        job.job_id, "COMPLETED_NOT_TRANSITIONED", job.current_stage,
                        {"policy": "MUTATING_REQUIRES_DURABLE_PROVENANCE"},
                        f"completed-not-transitioned:block:{latest['run_id']}",
                    )
                    recovered += 1
                    continue
                if job.current_stage is WorkflowStage.REVISION:
                    self.repository.mark_review_findings_consumed(
                        job.job_id, job.owner_id, job.review_round, f"recovery:{latest['run_id']}",
                    )
                if job.current_stage is WorkflowStage.REVIEW and hasattr(self.repository, "review_work_unit_for_round"):
                    round_number = job.review_round + 1
                    unit = self.repository.review_work_unit_for_round(job.job_id, job.owner_id, round_number)
                    self.repository.mark_review_result_consumed(
                        job.job_id, job.owner_id, round_number, unit.review_work_unit_id,
                    )
                next_stage = NEXT_STAGE[job.current_stage]
                if next_stage is WorkflowStage.DONE:
                    self.repository.update_job(
                        job.job_id, status=JobStatus.COMPLETED, current_stage=WorkflowStage.DONE,
                        attempt=0, resume_state=None, last_error=None, next_retry_at=None,
                    )
                else:
                    self.repository.update_job(
                        job.job_id, status=JobStatus.QUEUED, current_stage=next_stage,
                        attempt=0, resume_state="COMPLETED_NOT_TRANSITIONED_RECOVERED",
                        last_error=None, next_retry_at=None,
                    )
                self.repository.record_event(
                    job.job_id, "COMPLETED_NOT_TRANSITIONED", job.current_stage,
                    {"policy": "FINALIZED_WITHOUT_RERUN", "mutating": mutating},
                    f"completed-not-transitioned:finalize:{latest['run_id']}",
                )
                recovered += 1
                continue
            with self.repository.db:
                self.repository.db.execute(
                    "UPDATE supervisor_stage_runs SET status='INTERRUPTED',completed_at=?,error='PROCESS_INTERRUPTED' "
                    "WHERE job_id=? AND status='RUNNING'",
                    (utc_now(), job.job_id),
                )
            attempts = self.repository.stage_attempts(job.job_id, job.current_stage)
            if job.current_stage in SAFE_RECOVERY_STAGES and attempts < job.max_attempts_per_stage:
                self.repository.update_job(job.job_id, status=JobStatus.QUEUED,
                                           resume_state="INTERRUPTED_SAFE_RETRY", last_error="PROCESS_INTERRUPTED")
                self.repository.record_event(job.job_id, "RETRY_SCHEDULED", job.current_stage,
                                             {"reason": "PROCESS_INTERRUPTED"},
                                             f"recover:{job.job_id}:{job.current_stage.value}:{attempts}")
            else:
                self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                           resume_state="BLOCKED_REQUIRES_RECONCILIATION",
                                           last_error="PROCESS_INTERRUPTED")
                self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                             {"reason": "RECONCILIATION_REQUIRED"},
                                             f"blocked:{job.job_id}:{attempts}")
            recovered += 1
        return recovered

    def _schedule_failure(self, job: WorkflowJob, result: StageResult) -> WorkflowJob:
        attempts = self.repository.stage_attempts(job.job_id, job.current_stage)
        error = _safe_text(result.error or result.summary, 1000)
        if job.current_stage is WorkflowStage.SECURITY:
            updated = self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                                 resume_state="SECURITY_BLOCKED", last_error=error)
            self.repository.record_event(job.job_id, "SECURITY_FAILED", job.current_stage, {"error": error})
            return updated
        if job.current_stage is WorkflowStage.GIT_GATE:
            updated = self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                                 resume_state="GIT_GATE_BLOCKED", last_error=error)
            self.repository.record_event(job.job_id, "GIT_GATE_BLOCKED", job.current_stage, {"error": error})
            return updated
        if result.status is StageResultStatus.BLOCKED:
            updated = self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                                 resume_state="BLOCKED_REQUIRES_RECONCILIATION", last_error=error)
            self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage, {"error": error})
            return updated
        if attempts < job.max_attempts_per_stage:
            delay = min(self.retry_backoff_seconds * (2 ** max(0, attempts - 1)), 60)
            updated = self.repository.update_job(job.job_id, status=JobStatus.WAITING,
                                                 resume_state="RETRY_SCHEDULED", last_error=error,
                                                 next_retry_at=time.time() + delay)
            self.repository.record_event(job.job_id, "RETRY_SCHEDULED", job.current_stage,
                                         {"attempt": attempts, "backoff_seconds": delay})
            return updated
        updated = self.repository.update_job(job.job_id, status=JobStatus.FAILED,
                                             resume_state=None, last_error=error)
        self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                     {"attempt": attempts, "error": error})
        return updated

    def _run_selected(self, job: WorkflowJob) -> WorkflowJob:
        if job.resume_state == "PAUSED":
            return job
        runner = self.runners.get(job.current_stage)
        if not runner:
            return self.repository.update_job(job.job_id, status=JobStatus.BLOCKED,
                                              resume_state="RUNNER_NOT_CONFIGURED",
                                              last_error="RUNNER_NOT_CONFIGURED")
        started = self.repository.begin_stage(job)
        if not started:
            return self.status(job.job_id)
        run_id, attempt, idempotency_key = started
        context = StageContext(self.status(job.job_id), job.current_stage, attempt,
                               idempotency_key, self.timeout_seconds, self.repository)
        try:
            result = runner.run(context)
        except Exception as error:
            result = StageResult.failed("Stage runner raised a bounded error", type(error).__name__)
        self.repository.finish_stage(run_id, job.job_id, job.current_stage, result)
        current = self.status(job.job_id)
        if current.resume_state == "CANCEL_REQUESTED":
            return self.repository.update_job(job.job_id, status=JobStatus.CANCELED, resume_state=None)

        if job.current_stage is WorkflowStage.REVIEW:
            self.repository.record_event(
                job.job_id, "REVIEW_FINDINGS_RECEIVED", job.current_stage,
                {"status": result.status.value, "findings_count": len(result.review_findings)},
            )
            if result.status is StageResultStatus.PASS and result.review_findings:
                return self.repository.update_job(
                    job.job_id, status=JobStatus.BLOCKED, resume_state="REVIEW_FINDINGS_MISSING",
                    last_error="PASS_REVIEW_CONTAINED_FINDINGS",
                )
            if result.status is StageResultStatus.FAIL:
                review_round = job.review_round + 1
                if not result.review_findings:
                    updated = self.repository.update_job(
                        job.job_id, status=JobStatus.BLOCKED,
                        resume_state="REVIEW_FINDINGS_MISSING", last_error="REVIEW_FINDINGS_MISSING",
                    )
                    self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                                 {"reason": "REVIEW_FINDINGS_MISSING"})
                    return updated
                self.repository.persist_review_findings(
                    job.job_id, job.owner_id, review_round, result.review_findings,
                )
                self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                             {"reason": "REVIEW_FAIL", "review_round": review_round})
                if review_round >= job.max_review_rounds:
                    updated = self.repository.update_job(
                        job.job_id, status=JobStatus.BLOCKED, review_round=review_round,
                        resume_state="MAX_REVIEW_ROUNDS", last_error="MAX_REVIEW_ROUNDS",
                    )
                    self.repository.record_event(job.job_id, "STAGE_FAILED", job.current_stage,
                                                 {"reason": "MAX_REVIEW_ROUNDS", "review_round": review_round})
                    return updated
                updated = self.repository.update_job(
                    job.job_id, status=JobStatus.QUEUED, current_stage=WorkflowStage.REVISION,
                    review_round=review_round, attempt=0, resume_state=None,
                )
                self.repository.record_event(job.job_id, "STAGE_COMPLETED", job.current_stage,
                                             {"result": "FAIL_TO_REVISION", "review_round": review_round})
                return updated

        if result.status is not StageResultStatus.PASS:
            return self._schedule_failure(job, result)

        if job.current_stage is WorkflowStage.REVISION:
            self.repository.mark_review_findings_consumed(
                job.job_id, job.owner_id, job.review_round, idempotency_key,
            )

        next_stage = NEXT_STAGE[job.current_stage]
        if next_stage is WorkflowStage.DONE:
            updated = self.repository.update_job(
                job.job_id, status=JobStatus.COMPLETED, current_stage=WorkflowStage.DONE,
                attempt=0, resume_state=None, last_error=None,
            )
            self.repository.record_event(job.job_id, "STAGE_COMPLETED", job.current_stage, {"result": "PASS"})
            self.repository.record_event(job.job_id, "JOB_COMPLETED", WorkflowStage.DONE, {})
            return updated
        pause_requested = current.resume_state == "PAUSE_REQUESTED"
        updated = self.repository.update_job(
            job.job_id, status=JobStatus.WAITING if pause_requested else JobStatus.QUEUED,
            current_stage=next_stage, attempt=0,
            resume_state="PAUSED" if pause_requested else None, last_error=None, next_retry_at=None,
        )
        self.repository.record_event(job.job_id, "STAGE_COMPLETED", job.current_stage, {"result": "PASS"})
        return updated

    def run_once(self) -> WorkflowJob | None:
        self._require_lease()
        job = self.repository.queued_job()
        return self._run_selected(job) if job else None

    def run_job_once(self, job_id: str) -> WorkflowJob | None:
        self._require_lease()
        job = self.repository.queued_job(job_id)
        return self._run_selected(job) if job else self.status(job_id)

    def run_until_terminal(self, job_id: str, max_transitions=50) -> WorkflowJob:
        transitions = 0
        while transitions < max_transitions:
            job = self.status(job_id)
            if job.status in TERMINAL_JOB_STATUSES or job.status is JobStatus.BLOCKED or job.resume_state == "PAUSED":
                return job
            if job.status is JobStatus.WAITING and job.resume_state == "RETRY_SCHEDULED" and (job.next_retry_at or 0) > time.time():
                return job
            before = (job.status, job.current_stage, job.attempt, job.review_round, job.updated_at)
            after = self.run_job_once(job_id)
            if after is None:
                return self.status(job_id)
            now = (after.status, after.current_stage, after.attempt, after.review_round, after.updated_at)
            if now == before:
                return after
            transitions += 1
        job = self.repository.update_job(job_id, status=JobStatus.BLOCKED,
                                         resume_state="TRANSITION_LIMIT", last_error="TRANSITION_LIMIT")
        self.repository.record_event(job_id, "STAGE_FAILED", job.current_stage, {"reason": "TRANSITION_LIMIT"})
        return job
