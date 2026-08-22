from __future__ import annotations

import threading
from .supervisor_contracts import (
    AI_ROOT, CONTROL_PLANE_ROOT, JobStatus, LeaseLostError, ReviewFinding, ReviewResult,
    StageContext, StageResult, StageResultStatus, WorkflowJob, WorkflowStage,
)
from .supervisor_workflow import WorkflowSupervisor as BaseWorkflowSupervisor
from .supervisor_round2_common import REVIEW_RESULT_SCHEMA, ReviewTaskSpec

class DurableReviewRunner:
    """Consumes a previously submitted durable result; it never executes a reviewer model."""
    def run(self, context: StageContext) -> StageResult:
        if context.stage is not WorkflowStage.REVIEW:
            return StageResult(StageResultStatus.BLOCKED, "review runner scope denied", error="REVIEW_STAGE_SCOPE")
        round_number = context.job.review_round + 1
        try:
            unit = context.repository.review_work_unit_for_round(context.job.job_id, context.job.owner_id, round_number)
            context.repository.reconstruct_reviewer_task(context.job.job_id, context.job.owner_id, round_number)
            result = context.repository.submitted_review_result(
                context.job.job_id, context.job.owner_id, round_number, unit.review_work_unit_id,
            )
        except KeyError:
            return StageResult(StageResultStatus.BLOCKED, "durable review result pending", error="REVIEW_RESULT_PENDING")
        return result.to_stage_result()


class LeaseKeepingRunner:
    def __init__(self, inner, repository, token: str, ttl: float,
                 heartbeat_interval: float | None = None):
        self.inner, self.repository, self.token, self.ttl = inner, repository, token, ttl
        self.heartbeat_interval = heartbeat_interval or max(0.25, min(5.0, ttl / 3.0))
        self.cancel_succeeded = False
        self.external_execution_may_still_be_active = False
        self.durable_fence_persisted = False

    @property
    def cancellation_supported(self) -> bool:
        return bool(getattr(self.inner, "cancellation_supported", False))

    def cancel(self, execution_id: str | None = None, reason: str | None = None) -> bool:
        method = getattr(self.inner, "cancel", None)
        if not self.cancellation_supported or not callable(method):
            return False
        return bool(method(execution_id=execution_id, reason=reason))

    def run(self, context: StageContext) -> StageResult:
        stop = threading.Event()
        lost = threading.Event()
        cancel_attempted = threading.Event()

        def keeper():
            while not stop.wait(self.heartbeat_interval):
                if not self.repository.heartbeat_external(self.repository.path, self.token, self.ttl):
                    lost.set()
                    self.repository.lease_failed = True
                    method = getattr(self.inner, "cancel", None)
                    supported = getattr(self.inner, "cancellation_supported", callable(method))
                    if supported and callable(method):
                        cancel_attempted.set()
                        try:
                            try:
                                self.cancel_succeeded = bool(method(reason="LEASE_LOST"))
                            except TypeError:
                                self.cancel_succeeded = bool(method())
                        except Exception:
                            self.cancel_succeeded = False
                    if not self.cancel_succeeded:
                        self.external_execution_may_still_be_active = True
                        self.repository.external_execution_may_still_be_active = True
                        execution_id = getattr(self.inner, "execution_id", None)
                        work_unit_id = getattr(self.inner, "work_unit_id", None)
                        reason = ("LEASE_LOST_CANCELLATION_FAILED" if cancel_attempted.is_set()
                                  else "LEASE_LOST_CANCELLATION_UNSUPPORTED")
                        persist_fence = getattr(self.repository, "persist_mutation_fence_external", None)
                        context_job = getattr(context, "job", None)
                        self.durable_fence_persisted = bool(persist_fence and context_job and persist_fence(
                            self.repository.path, context_job.job_id, reason,
                            work_unit_id, execution_id,
                        ))
                    return

        thread = threading.Thread(target=keeper, name="supervisor-lease-keeper", daemon=True)
        thread.start()
        try:
            result = self.inner.run(context)
        finally:
            stop.set()
            thread.join(timeout=max(1.0, self.heartbeat_interval * 2))
        if lost.is_set():
            if self.cancel_succeeded:
                raise LeaseLostError("lease lost during runner; internal cancellation propagated")
            raise LeaseLostError(
                "EXTERNAL_EXECUTION_MAY_STILL_BE_ACTIVE; BLOCKED_REQUIRES_RECONCILIATION; "
                + ("durable fence persisted; " if self.durable_fence_persisted
                   else "DURABLE_FENCE_PERSIST_FAILED; ")
                + ("cancellation failed" if cancel_attempted.is_set() else "cancellation unsupported")
            )
        return result


class Round2WorkflowSupervisor(BaseWorkflowSupervisor):
    def acquire_singleton(self, pid: int | None = None) -> bool:
        acquired = super().acquire_singleton(pid)
        if acquired and hasattr(self.repository, "set_active_lease"):
            self.repository.set_active_lease(self.lock_token)
        return acquired

    def release_singleton(self) -> None:
        try:
            super().release_singleton()
        finally:
            if hasattr(self.repository, "set_active_lease"):
                self.repository.set_active_lease(None)

    def _run_selected(self, job: WorkflowJob) -> WorkflowJob:
        self._require_lease()
        if (job.current_stage in {WorkflowStage.PRODUCER, WorkflowStage.REVISION}
                and (getattr(self.repository, "external_execution_may_still_be_active", False)
                     or self.repository.has_active_mutation_fence())):
            return self.repository.update_job(
                job.job_id, status=JobStatus.BLOCKED,
                resume_state="BLOCKED_REQUIRES_RECONCILIATION",
                last_error="EXTERNAL_EXECUTION_MAY_STILL_BE_ACTIVE",
            )
        original = self.runners.get(job.current_stage)
        if original is None:
            return super()._run_selected(job)
        wrapped = LeaseKeepingRunner(original, self.repository, self.lock_token, self.lock_ttl_seconds)
        self.runners[job.current_stage] = wrapped
        try:
            result = super()._run_selected(job)
            self._require_lease()
            if job.current_stage is WorkflowStage.REVIEW:
                self.repository.reconcile_submitted_review_results()
            return result
        finally:
            self.runners[job.current_stage] = original

    def _default_review_spec(self, job: WorkflowJob, review_round: int) -> ReviewTaskSpec:
        prompt = (
            f"Independently review workflow job {job.job_id}, review round {review_round}. "
            "Read only within the allowed repository paths. Return only the expected structured review schema."
        )
        return ReviewTaskSpec(
            AI_ROOT, (CONTROL_PLANE_ROOT, AI_ROOT / "docs"), prompt, True, job.risk_level,
            min(float(self.timeout_seconds), 3600.0), "REVIEW", REVIEW_RESULT_SCHEMA,
        )

    def _prepare_review(self, job: WorkflowJob) -> WorkflowJob:
        review_round = job.review_round + 1
        try:
            unit = self.repository.review_work_unit_for_round(job.job_id, job.owner_id, review_round)
        except KeyError:
            unit = self.repository.create_review_work_unit(
                job.job_id, job.owner_id, review_round, self._default_review_spec(job, review_round),
                review_work_unit_id=f"review-{job.job_id}-{review_round}",
            )
        if job.metadata.get("supervisor_demo"):
            try:
                self.repository.submitted_review_result(job.job_id, job.owner_id, review_round, unit.review_work_unit_id)
            except KeyError:
                candidate_file = next(iter(unit.candidate_identity.candidate_paths), None)
                if not candidate_file:
                    raise ValueError("demo review requires a bounded candidate file")
                synthetic = (ReviewResult("FAIL", (ReviewFinding(
                    "BLOCKING", candidate_file,
                    "synthetic demo evidence", "synthetic demo revision",
                ),)) if job.review_round == 0 else ReviewResult("PASS"))
                self.repository.submit_review_result(job.job_id, job.owner_id, review_round,
                                                     unit.review_work_unit_id, synthetic)
        try:
            self.repository.submitted_review_result(job.job_id, job.owner_id, review_round, unit.review_work_unit_id)
        except KeyError:
            if job.status is not JobStatus.WAITING or job.resume_state != "REVIEW_RESULT_PENDING":
                job = self.repository.update_job(job.job_id, status=JobStatus.WAITING,
                                                 resume_state="REVIEW_RESULT_PENDING", next_retry_at=None)
                self.repository.record_event(job.job_id, "REVIEW_RESULT_PENDING", WorkflowStage.REVIEW,
                                             {"review_round": review_round, "review_work_unit_id": unit.review_work_unit_id},
                                             f"review-pending:{job.job_id}:{review_round}")
            return job
        if job.status is JobStatus.WAITING and job.resume_state == "REVIEW_RESULT_PENDING":
            return self.repository.update_job(job.job_id, status=JobStatus.QUEUED, resume_state=None)
        return job

    def run_once(self) -> WorkflowJob | None:
        self._require_lease()
        job = self.repository.queued_job()
        if job and job.current_stage is WorkflowStage.REVIEW:
            job = self._prepare_review(job)
            if job.status is not JobStatus.QUEUED:
                return job
        return self._run_selected(job) if job else None

    def run_job_once(self, job_id: str) -> WorkflowJob | None:
        self._require_lease()
        job = self.status(job_id)
        if job.current_stage is WorkflowStage.REVIEW and job.status in {JobStatus.QUEUED, JobStatus.WAITING}:
            job = self._prepare_review(job)
            if job.status is not JobStatus.QUEUED:
                return job
        queued = self.repository.queued_job(job_id)
        return self._run_selected(queued) if queued else self.status(job_id)
