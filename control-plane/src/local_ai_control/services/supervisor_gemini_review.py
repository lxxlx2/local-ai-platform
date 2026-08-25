from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict

from .cloud_egress import CloudEgressDenied
from .gemini_provider import GeminiProviderError
from .gemini_review_gateway import GeminiReviewGateway
from .provider_credentials import ProviderCredentialError, read_keychain_secret
from .provider_router import PrivacyMode
from .supervisor_contracts import StageContext, StageResult, StageResultStatus, WorkflowStage, _json_exact, utc_now
from .supervisor_local_qwen import LocalWorktreeDurableReviewRunner, local_qwen_runners


_TABLE = "supervisor_gemini_review_recommendations"


def _ensure_schema(repository) -> None:
    repository.db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE}(
          review_work_unit_id TEXT PRIMARY KEY,
          job_id TEXT NOT NULL,
          owner_id TEXT NOT NULL,
          review_round INTEGER NOT NULL,
          patch_sha256 TEXT NOT NULL,
          status TEXT NOT NULL,
          payload_json TEXT,
          payload_sha256 TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    repository.db.commit()


def _privacy_for_job(job) -> PrivacyMode:
    raw = str((job.metadata or {}).get("privacy_mode", PrivacyMode.RESTRICTED.value)).upper()
    try:
        return PrivacyMode(raw)
    except ValueError as error:
        raise ValueError("invalid supervisor privacy_mode") from error


def _recommendation_row(repository, review_work_unit_id: str):
    _ensure_schema(repository)
    return repository.db.execute(
        f"SELECT * FROM {_TABLE} WHERE review_work_unit_id=?",
        (review_work_unit_id,),
    ).fetchone()


def load_gemini_recommendation(repository, review_work_unit_id: str) -> dict | None:
    row = _recommendation_row(repository, review_work_unit_id)
    if row is None:
        return None
    if row["status"] != "READY":
        return {
            "status": row["status"],
            "patch_sha256": row["patch_sha256"],
            "created_at": row["created_at"],
        }
    payload = row["payload_json"] or ""
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if digest != row["payload_sha256"]:
        raise ValueError("Gemini recommendation integrity mismatch")
    decoded = json.loads(payload)
    if decoded.get("patch_sha256") != row["patch_sha256"]:
        raise ValueError("Gemini recommendation patch binding mismatch")
    return decoded


def _persist_status(repository, unit, job, review_round: int, status: str) -> None:
    _ensure_schema(repository)
    existing = _recommendation_row(repository, unit.review_work_unit_id)
    if existing:
        if existing["patch_sha256"] != unit.patch_sha256:
            raise ValueError("Gemini recommendation candidate is stale")
        return
    with repository.db:
        repository.db.execute(
            f"INSERT INTO {_TABLE} VALUES(?,?,?,?,?,?,?,?,?)",
            (
                unit.review_work_unit_id,
                job.job_id,
                str(job.owner_id),
                int(review_round),
                unit.patch_sha256,
                status,
                None,
                None,
                utc_now(),
            ),
        )


def _persist_recommendation(repository, unit, job, review_round: int, gated) -> dict:
    findings = [asdict(item) for item in gated.review.findings]
    payload = {
        "status": "READY",
        "review_work_unit_id": unit.review_work_unit_id,
        "job_id": job.job_id,
        "review_round": int(review_round),
        "patch_sha256": unit.patch_sha256,
        "verdict": gated.review.verdict,
        "summary": gated.review.summary[:4096],
        "findings": findings,
        "findings_count": len(findings),
        "model": gated.review.model,
        "latency_seconds": gated.review.latency_seconds,
        "privacy": gated.egress.privacy.value,
        "redactions": list(gated.egress.redactions),
        "egress_reason": gated.egress.reason,
        "egress_original_sha256": gated.egress.original_sha256,
        "egress_material_sha256": gated.egress.material_sha256,
        "created_at": utc_now(),
    }
    encoded = _json_exact(payload, 128_000)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    _ensure_schema(repository)
    existing = _recommendation_row(repository, unit.review_work_unit_id)
    if existing:
        if existing["patch_sha256"] != unit.patch_sha256:
            raise ValueError("Gemini recommendation candidate is stale")
        return load_gemini_recommendation(repository, unit.review_work_unit_id) or payload
    with repository.db:
        repository.db.execute(
            f"INSERT INTO {_TABLE} VALUES(?,?,?,?,?,?,?,?,?)",
            (
                unit.review_work_unit_id,
                job.job_id,
                str(job.owner_id),
                int(review_round),
                unit.patch_sha256,
                "READY",
                encoded,
                digest,
                payload["created_at"],
            ),
        )
    return payload


def _review_material(repository, job, review_round: int) -> str:
    task = repository.reconstruct_reviewer_task(job.job_id, job.owner_id, review_round)
    patch = repository.reconstruct_reviewer_patch(job.job_id, job.owner_id, review_round)
    objective = task.task_objective.to_mapping() if task.task_objective is not None else None
    payload = {
        "review_instructions": task.task_prompt,
        "objective": objective,
        "candidate_patch": patch,
    }
    return _json_exact(payload, 256_000)


class GeminiAdvisoryReviewRunner(LocalWorktreeDurableReviewRunner):
    """Run Gemini once per immutable patch, then preserve the human review boundary."""

    def __init__(self, gateway: GeminiReviewGateway | None = None):
        self.gateway = gateway or GeminiReviewGateway()

    @staticmethod
    def _with_keychain_gemini(callback):
        existing = os.environ.get("GEMINI_API_KEY")
        inserted = False
        if not existing:
            os.environ["GEMINI_API_KEY"] = read_keychain_secret("gemini")
            inserted = True
        try:
            return callback()
        finally:
            if inserted:
                os.environ.pop("GEMINI_API_KEY", None)

    def _ensure_advisory(self, context: StageContext, review_round: int, unit) -> dict | None:
        existing = load_gemini_recommendation(context.repository, unit.review_work_unit_id)
        if existing is not None:
            return existing
        privacy = _privacy_for_job(context.job)
        if privacy is PrivacyMode.PRIVATE:
            _persist_status(context.repository, unit, context.job, review_round, "SKIPPED_PRIVATE")
            return load_gemini_recommendation(context.repository, unit.review_work_unit_id)
        try:
            material = _review_material(context.repository, context.job, review_round)
            gated = self._with_keychain_gemini(
                lambda: self.gateway.review(material=material, privacy=privacy)
            )
            return _persist_recommendation(
                context.repository, unit, context.job, review_round, gated
            )
        except (CloudEgressDenied, GeminiProviderError, ProviderCredentialError, ValueError) as error:
            status = f"UNAVAILABLE_{type(error).__name__}"[:64]
            _persist_status(context.repository, unit, context.job, review_round, status)
            return load_gemini_recommendation(context.repository, unit.review_work_unit_id)

    def run(self, context: StageContext) -> StageResult:
        if context.stage is not WorkflowStage.REVIEW:
            return StageResult(
                StageResultStatus.BLOCKED,
                "review runner scope denied",
                error="REVIEW_STAGE_SCOPE",
            )
        review_round = context.job.review_round + 1
        try:
            unit = context.repository.review_work_unit_for_round(
                context.job.job_id, context.job.owner_id, review_round
            )
            advisory = self._ensure_advisory(context, review_round, unit)
        except KeyError:
            advisory = None
        result = super().run(context)
        metrics = dict(result.metrics)
        if advisory:
            metrics.update(
                {
                    "gemini_advisory_status": advisory.get("status"),
                    "gemini_verdict": advisory.get("verdict"),
                    "gemini_model": advisory.get("model"),
                    "gemini_findings": advisory.get("findings_count", 0),
                    "gemini_patch_sha256": advisory.get("patch_sha256"),
                }
            )
        return StageResult(
            result.status,
            result.summary,
            error=result.error,
            retryable=result.retryable,
            metrics=metrics,
            artifacts=result.artifacts,
        )


def gemini_local_qwen_runners(repo_root, *, enabled: bool = False, gateway=None):
    runners = local_qwen_runners(repo_root, enabled=enabled)
    runners[WorkflowStage.REVIEW] = GeminiAdvisoryReviewRunner(gateway=gateway)
    return runners
