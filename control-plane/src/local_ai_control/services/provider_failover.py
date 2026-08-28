"""Deterministic, durable OpenAI Codex -> Local Qwen provider handoff.

The controller changes execution providers for one existing Supervisor job.  It
does not create jobs, execute model turns, grant approvals, or widen tool
authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

from .codex_availability import (
    CodexAvailabilityEvidence,
    CodexAvailabilityMonitor,
    CodexAvailabilityStatus,
)
from .codex_qwen_workspace import WorkspacePolicyError, validate_workspace
from .models import QWEN38
from .supervisor_contracts import JobStatus


class ProviderIdentity(str, Enum):
    OPENAI_CODEX = "OPENAI_CODEX"
    LOCAL_QWEN = "LOCAL_QWEN"


class ProviderState(str, Enum):
    CLOUD_CODEX = "CLOUD_CODEX"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    LOCAL_PREFLIGHT = "LOCAL_PREFLIGHT"
    LOCAL_QWEN = "LOCAL_QWEN"
    CONTINUE_SAME_JOB = "CONTINUE_SAME_JOB"
    SAFE_BOUNDARY = "SAFE_BOUNDARY"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"


class AvailabilityEvidenceSource(str, Enum):
    SUPPORTED_QUOTA_TELEMETRY = "SUPPORTED_QUOTA_TELEMETRY"
    ACTIVE_REQUEST_ERROR = "ACTIVE_REQUEST_ERROR"
    PROVIDER_PROBE = "PROVIDER_PROBE"


class FailoverDenied(RuntimeError):
    pass


class LocalPreflightFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalPreflightAttestation:
    workspace_path: str
    branch: str
    model_id: str
    bridge_backend: str
    bridge_tool: str
    route: str
    network_policy: str
    permissions_profile: str

    def safe_payload(self) -> dict[str, str]:
        return {
            "workspace_path": self.workspace_path,
            "branch": self.branch,
            "model_id": self.model_id,
            "bridge_backend": self.bridge_backend,
            "bridge_tool": self.bridge_tool,
            "route": self.route,
            "network_policy": self.network_policy,
            "permissions_profile": self.permissions_profile,
        }


class LocalFailoverPreflight:
    """Read-only exact route/workspace attestation for the local provider."""

    def __init__(self, *, qwen_health_probe, bridge_health_probe,
                 workspace_validator=validate_workspace):
        self.qwen_health_probe = qwen_health_probe
        self.bridge_health_probe = bridge_health_probe
        self.workspace_validator = workspace_validator

    @staticmethod
    def _qwen_healthy(value: object) -> bool:
        return bool(
            isinstance(value, dict)
            and value.get("status") == "healthy"
            and value.get("model") == QWEN38.model_id
        )

    @staticmethod
    def _bridge_healthy(value: object) -> bool:
        return bool(
            isinstance(value, dict)
            and value.get("status") == "healthy"
            and value.get("backend") == QWEN38.model_id
            and value.get("tool") == "exec_command"
        )

    def attest(self, job, *, expected_workspace: str, expected_branch: str) -> LocalPreflightAttestation:
        if str(Path(job.project_scope).resolve()) != str(Path(expected_workspace).resolve()):
            raise LocalPreflightFailed("LOCAL_PREFLIGHT_JOB_WORKSPACE_MISMATCH")
        try:
            workspace = self.workspace_validator(job.project_scope)
        except (WorkspacePolicyError, OSError, ValueError) as error:
            raise LocalPreflightFailed("LOCAL_PREFLIGHT_WORKSPACE_DENIED") from error
        if str(workspace.root) != str(Path(expected_workspace).resolve()) or workspace.branch != expected_branch:
            raise LocalPreflightFailed("LOCAL_PREFLIGHT_BRANCH_MISMATCH")
        try:
            qwen_health = self.qwen_health_probe()
            bridge_health = self.bridge_health_probe()
        except Exception as error:
            raise LocalPreflightFailed("LOCAL_PREFLIGHT_HEALTH_UNAVAILABLE") from error
        if not self._qwen_healthy(qwen_health):
            raise LocalPreflightFailed("LOCAL_PREFLIGHT_QWEN_IDENTITY_MISMATCH")
        if not self._bridge_healthy(bridge_health):
            raise LocalPreflightFailed("LOCAL_PREFLIGHT_BRIDGE_IDENTITY_MISMATCH")
        return LocalPreflightAttestation(
            str(workspace.root), workspace.branch, QWEN38.model_id, QWEN38.model_id,
            "exec_command", "LOCALHOST_RESPONSES_8010_TO_QWEN38_8001",
            "WORKSPACE_NETWORK_DISABLED", "LOCAL_QWEN_BOUNDED_PRODUCER",
        )


class ProviderFailoverController:
    """Fail-closed state machine over one existing durable Supervisor job."""

    def __init__(self, repository, local_preflight: LocalFailoverPreflight,
                 monitor: CodexAvailabilityMonitor | None = None):
        self.repository = repository
        self.local_preflight = local_preflight
        self.monitor = monitor or CodexAvailabilityMonitor()

    @staticmethod
    def objective_digest(job) -> str:
        # The job request hash separately binds the complete immutable create-job
        # manifest.  This digest gives operators a non-content objective handle.
        return hashlib.sha256(job.title.encode("utf-8")).hexdigest()

    def register_job(self, job_id: str) -> dict:
        job = self.repository.get_job(job_id)
        workspace = validate_workspace(job.project_scope)
        return self.repository.initialize_provider_state(
            job_id, workspace_path=str(workspace.root), branch=workspace.branch,
            objective_sha256=self.objective_digest(job),
            current_provider=ProviderIdentity.OPENAI_CODEX,
            state=ProviderState.CLOUD_CODEX,
        )

    def _validated_state(self, job_id: str) -> tuple[object, dict]:
        job = self.repository.get_job(job_id)
        state = self.repository.provider_state(job_id)
        if str(Path(job.project_scope).resolve()) != state["workspace_path"]:
            raise FailoverDenied("DURABLE_JOB_WORKSPACE_BINDING_MISMATCH")
        if self.objective_digest(job) != state["objective_sha256"]:
            raise FailoverDenied("DURABLE_JOB_OBJECTIVE_BINDING_MISMATCH")
        try:
            workspace = validate_workspace(job.project_scope)
        except (WorkspacePolicyError, OSError, ValueError) as error:
            raise FailoverDenied("DURABLE_JOB_WORKSPACE_UNAVAILABLE") from error
        if str(workspace.root) != state["workspace_path"] or workspace.branch != state["branch"]:
            raise FailoverDenied("DURABLE_JOB_BRANCH_BINDING_MISMATCH")
        return job, state

    @staticmethod
    def _signal(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", str(value)):
            raise ValueError("invalid failover signal id")
        return str(value)

    @staticmethod
    def _trigger_allowed(status: CodexAvailabilityStatus,
                         source: AvailabilityEvidenceSource) -> bool:
        if status is CodexAvailabilityStatus.QUOTA_EXHAUSTED:
            return source in {
                AvailabilityEvidenceSource.SUPPORTED_QUOTA_TELEMETRY,
                AvailabilityEvidenceSource.ACTIVE_REQUEST_ERROR,
            }
        if status is CodexAvailabilityStatus.RATE_LIMITED:
            return source is AvailabilityEvidenceSource.ACTIVE_REQUEST_ERROR
        if status is CodexAvailabilityStatus.PROVIDER_UNAVAILABLE:
            return source in {
                AvailabilityEvidenceSource.ACTIVE_REQUEST_ERROR,
                AvailabilityEvidenceSource.PROVIDER_PROBE,
            }
        return False

    def _transition(self, job_id: str, signal: str, suffix: str, *,
                    from_provider: ProviderIdentity, to_provider: ProviderIdentity,
                    from_state: ProviderState, to_state: ProviderState,
                    reason: CodexAvailabilityStatus, source: AvailabilityEvidenceSource,
                    execution_id: str | None = None, safe_boundary: bool = False,
                    mutating_step: bool = False) -> dict:
        return self.repository.append_provider_transition(
            job_id, idempotency_key=f"failover:{signal}:{suffix}",
            from_provider=from_provider, to_provider=to_provider,
            from_state=from_state, to_state=to_state, reason=reason.name,
            evidence_source=source, execution_id=execution_id,
            safe_boundary=safe_boundary, mutating_step=mutating_step,
        )

    def failover(self, job_id: str, evidence: CodexAvailabilityEvidence, *,
                 evidence_source: AvailabilityEvidenceSource, signal_id: str,
                 execution_id: str | None = None) -> dict:
        signal = self._signal(signal_id)
        status = self.monitor.classify(evidence)
        if not self._trigger_allowed(status, evidence_source):
            raise FailoverDenied(f"FAILOVER_EVIDENCE_DENIED:{status.name}")
        try:
            state = self.repository.provider_state(job_id)
        except KeyError:
            state = self.register_job(job_id)
        job, state = self._validated_state(job_id)
        if state["current_provider"] == ProviderIdentity.LOCAL_QWEN.value and state["state"] in {
            ProviderState.LOCAL_QWEN.value, ProviderState.CONTINUE_SAME_JOB.value,
        }:
            return state
        if state["state"] == ProviderState.BLOCKED.value:
            raise FailoverDenied("FAILOVER_JOB_BLOCKED_REQUIRES_RECONCILIATION")
        if state["state"] == ProviderState.CLOUD_CODEX.value:
            self._transition(
                job_id, signal, "handoff", from_provider=ProviderIdentity.OPENAI_CODEX,
                to_provider=ProviderIdentity.OPENAI_CODEX, from_state=ProviderState.CLOUD_CODEX,
                to_state=ProviderState.HANDOFF_PENDING, reason=status, source=evidence_source,
                execution_id=execution_id,
            )
            state = self.repository.provider_state(job_id)
        if state["state"] == ProviderState.HANDOFF_PENDING.value:
            self._transition(
                job_id, signal, "preflight", from_provider=ProviderIdentity.OPENAI_CODEX,
                to_provider=ProviderIdentity.OPENAI_CODEX, from_state=ProviderState.HANDOFF_PENDING,
                to_state=ProviderState.LOCAL_PREFLIGHT, reason=status, source=evidence_source,
                execution_id=execution_id,
            )
            state = self.repository.provider_state(job_id)
        if state["state"] != ProviderState.LOCAL_PREFLIGHT.value:
            raise FailoverDenied("FAILOVER_STATE_RECONCILIATION_REQUIRED")
        try:
            attestation = self.local_preflight.attest(
                job, expected_workspace=state["workspace_path"], expected_branch=state["branch"],
            )
        except LocalPreflightFailed as error:
            self._transition(
                job_id, signal, "blocked", from_provider=ProviderIdentity.OPENAI_CODEX,
                to_provider=ProviderIdentity.OPENAI_CODEX, from_state=ProviderState.LOCAL_PREFLIGHT,
                to_state=ProviderState.BLOCKED, reason=status, source=evidence_source,
                execution_id=execution_id,
            )
            self.repository.update_job(
                job_id, status=JobStatus.BLOCKED,
                resume_state="LOCAL_FAILOVER_PREFLIGHT_BLOCKED", last_error=str(error),
            )
            return self.repository.provider_state(job_id)

        # The attestation contains structural route identity only.  Its digest is
        # audit evidence; no health response body or credential is persisted.
        attestation_sha = hashlib.sha256(
            json.dumps(attestation.safe_payload(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._transition(
            job_id, signal, f"local-{attestation_sha[:16]}",
            from_provider=ProviderIdentity.OPENAI_CODEX, to_provider=ProviderIdentity.LOCAL_QWEN,
            from_state=ProviderState.LOCAL_PREFLIGHT, to_state=ProviderState.LOCAL_QWEN,
            reason=status, source=evidence_source, execution_id=execution_id,
        )
        self._transition(
            job_id, signal, "continue", from_provider=ProviderIdentity.LOCAL_QWEN,
            to_provider=ProviderIdentity.LOCAL_QWEN, from_state=ProviderState.LOCAL_QWEN,
            to_state=ProviderState.CONTINUE_SAME_JOB, reason=status, source=evidence_source,
            execution_id=execution_id,
        )
        return self.repository.provider_state(job_id)

    def recover_cloud_at_safe_boundary(
        self, job_id: str, evidence: CodexAvailabilityEvidence, *, signal_id: str,
        safe_boundary: bool, mutating_step: bool,
        evidence_source: AvailabilityEvidenceSource = AvailabilityEvidenceSource.PROVIDER_PROBE,
    ) -> dict:
        signal = self._signal(signal_id)
        status = self.monitor.classify(evidence)
        if status is not CodexAvailabilityStatus.AVAILABLE:
            raise FailoverDenied(f"CLOUD_RECOVERY_EVIDENCE_DENIED:{status.name}")
        _job, state = self._validated_state(job_id)
        if state["current_provider"] != ProviderIdentity.LOCAL_QWEN.value:
            return state
        if mutating_step or not safe_boundary:
            # Deliberately no write: merely observing cloud recovery cannot
            # interrupt or mutate an active local producer step.
            return state
        current_state = ProviderState(state["state"])
        if current_state is ProviderState.CONTINUE_SAME_JOB:
            self._transition(
                job_id, signal, "safe-boundary", from_provider=ProviderIdentity.LOCAL_QWEN,
                to_provider=ProviderIdentity.LOCAL_QWEN, from_state=current_state,
                to_state=ProviderState.SAFE_BOUNDARY, reason=status,
                source=evidence_source, safe_boundary=True,
            )
            current_state = ProviderState.SAFE_BOUNDARY
        if current_state is ProviderState.SAFE_BOUNDARY:
            self._transition(
                job_id, signal, "review", from_provider=ProviderIdentity.LOCAL_QWEN,
                to_provider=ProviderIdentity.OPENAI_CODEX, from_state=current_state,
                to_state=ProviderState.REVIEW, reason=status,
                source=evidence_source, safe_boundary=True,
            )
        return self.repository.provider_state(job_id)


def http_json_health(url: str) -> dict:
    """Bounded loopback-only health probe for explicit operator wiring."""
    if url not in {"http://127.0.0.1:8001/health", "http://127.0.0.1:8010/health"}:
        raise ValueError("failover health URL not allowlisted")
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise LocalPreflightFailed("LOCAL_PREFLIGHT_HEALTH_UNAVAILABLE") from error
