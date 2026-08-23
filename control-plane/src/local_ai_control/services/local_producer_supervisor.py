"""Supervisor-safe Local Producer adapter with revision candidate binding."""
from __future__ import annotations

import hashlib
from pathlib import Path
import uuid

from local_ai_control.services.local_producer import (
    LocalPatchProducer, LocalProducerError, discover_context_paths, require_safe_worktree,
)
from local_ai_control.services.qwen38_runtime import Qwen38Provider
from local_ai_control.services.supervisor_contracts import (
    CandidateIdentityProvider, CodexTaskSpec, StageResult, StageResultStatus,
)


class SupervisorLocalProducerTaskRunner:
    """Durable task runner that accepts dirty state only when candidate-bound.

    Initial Producer work starts from a clean feature branch. Revision work may
    legitimately start from a dirty candidate; in that case the immutable
    CandidateIdentity carried by the durable work unit must match the current
    worktree exactly before another model patch is allowed.
    """
    cancellation_supported = False

    def __init__(self, provider=None, *, attempts: int = 2):
        self.provider = provider or Qwen38Provider()
        self.attempts = attempts

    def cancel(self, execution_id: str | None = None, reason: str | None = None) -> bool:
        return False

    @staticmethod
    def _validate_candidate_state(spec: CodexTaskSpec) -> None:
        if spec.candidate_identity is None:
            require_safe_worktree(spec.repo_root)
            return
        probe = CandidateIdentityProvider(spec.repo_root)
        current = probe.snapshot(spec.candidate_identity.base_commit_sha)
        if not current.same_candidate(spec.candidate_identity):
            raise LocalProducerError("durable revision candidate no longer matches current worktree")

    def run_task(self, spec: CodexTaskSpec, execution_id: str) -> StageResult:
        try:
            uuid.UUID(execution_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("execution_id must be a canonical UUID") from exc
        try:
            validated = spec.validate()
            self._validate_candidate_state(spec)
        except Exception as exc:
            return StageResult(
                StageResultStatus.BLOCKED, "Local Producer candidate state is unsafe or stale",
                error=type(exc).__name__,
                metrics={"detail_sha256": hashlib.sha256(str(exc).encode()).hexdigest()},
            )
        manifest_paths = {str(item["path"]) for item in validated["safe_file_manifest"]}
        discovered = discover_context_paths(spec.task_prompt, spec.repo_root)
        selected = tuple(path for path in discovered if path in manifest_paths)
        if not selected:
            selected = tuple(sorted(manifest_paths))[:6]
        producer = LocalPatchProducer(
            self.provider, repo_root=spec.repo_root,
            write_roots=tuple(Path(path) for path in validated["write_roots"]),
        )
        try:
            proposal = producer.propose(spec.task_prompt, selected, attempts=self.attempts)
            producer.apply(proposal)
        except LocalProducerError as exc:
            return StageResult(
                StageResultStatus.BLOCKED, "Local Producer could not produce a safe applicable patch",
                error=type(exc).__name__,
                metrics={"detail_sha256": hashlib.sha256(str(exc).encode()).hexdigest()},
            )
        return StageResult.passed(
            "Local Qwen3.8 produced and applied a candidate-bound policy-validated patch; tests not yet run",
            artifacts=({
                "kind": "local_patch", "reference": f"sha256:{proposal.patch_sha256}",
                "size_bytes": len(proposal.patch.encode()),
            },),
            metrics={
                "provider": "Qwen3.8-27B-8bit", "files_changed": len(proposal.paths),
                "tests_executed": False, "candidate_bound": spec.candidate_identity is not None,
            },
        )
