from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

from local_ai_control.services.security import SecretFirewall
from .codex_qwen_workspace import validate_workspace, WorkspacePolicyError
from .models import QWEN38
from .supervisor_codex import PersistedCodexStageRunner
from .supervisor_contracts import (
    CONTROL_PLANE_PYTHON,
    MAX_MUTATING_JOBS_IN_SYSTEM,
    MAX_WORK_UNIT_PROMPT_BYTES,
    CodexTaskSpec,
    JobStatus,
    RepoAccessPolicy,
    RepoWritePolicy,
    ReviewResult,
    StageContext,
    StageResult,
    StageResultStatus,
    WorkflowJob,
    WorkflowStage,
    _bounded,
    _json_exact,
    _safe_audit_value,
    _safe_json,
    utc_now,
)
from .supervisor_round2 import Round2SupervisorRepository
from .supervisor_round2_common import (
    REVIEW_RESULT_SCHEMA,
    ReviewTaskSpec,
    TaskObjective,
    _canonical_digest,
    recursive_private_sanitize,
)
from .supervisor_round2_workflow import DurableReviewRunner, Round2WorkflowSupervisor
from .supervisor_round2_security import Round2SecurityRunner
from .supervisor_runners import GitGateRunner, SafeCommandPolicy, StaticPassRunner


DEFAULT_BRIDGE_HEALTH_URL = "http://127.0.0.1:8010/health"
LOCAL_QWEN_SUPERVISOR_DB = Path("/Users/jerson/AI/runtime/supervisor-local-qwen/supervisor.db")
LOCAL_QWEN_EXECUTION_TRACE_ROOT = Path("/Users/jerson/AI/runtime/supervisor-local-qwen/executions")
SAFE_ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
MAX_TRACE_STREAM_BYTES = 1_000_000
MAX_TRACE_LINES = 10_000


class LocalProducerExecutionUncertain(RuntimeError):
    """External Codex execution may have mutated the worktree before failing."""


class LocalQwenExecutionTrace:
    """Persist bounded structural Codex progress without response or tool content."""

    def __init__(self, root: Path = LOCAL_QWEN_EXECUTION_TRACE_ROOT):
        self.root = Path(root)

    @staticmethod
    def _stream_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _safe_event_type(value) -> str:
        value = str(value or "UNKNOWN")
        return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value) else "UNKNOWN"

    def summarize(
        self,
        execution_id: str,
        *,
        started_at: str,
        ended_at: str,
        duration_seconds: float,
        timed_out: bool,
        return_code: int | None,
        stdout=None,
        stderr=None,
    ) -> dict:
        identifier = str(uuid.UUID(str(execution_id)))
        raw_stdout = self._stream_text(stdout)
        raw_stderr = self._stream_text(stderr)
        stdout_bytes = len(raw_stdout.encode("utf-8", errors="replace"))
        stderr_bytes = len(raw_stderr.encode("utf-8", errors="replace"))
        lines = raw_stdout[:MAX_TRACE_STREAM_BYTES].splitlines()[:MAX_TRACE_LINES]
        event_counts: dict[str, int] = {}
        malformed = completed = in_progress = command_execution = agent_message = 0
        last_type = None
        for line in lines:
            try:
                event = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                malformed += 1
                continue
            if not isinstance(event, dict):
                malformed += 1
                continue
            nested = event.get("item") if isinstance(event.get("item"), dict) else {}
            event_type = self._safe_event_type(event.get("type") or nested.get("type"))
            item_type = self._safe_event_type(nested.get("type")) if nested else "UNKNOWN"
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            last_type = event_type
            status = str(event.get("status") or nested.get("status") or "").lower()
            completed += int(status in {"completed", "complete", "done"} or event_type.endswith(".completed"))
            in_progress += int(status in {"in_progress", "running", "started"} or event_type.endswith(".started"))
            command_execution += int("command_execution" in event_type or "command_execution" in item_type)
            agent_message += int("agent_message" in event_type or "agent_message" in item_type)
        return {
            "schema_version": "0.1",
            "execution_id": identifier,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": round(float(duration_seconds), 3),
            "timed_out": bool(timed_out),
            "return_code": int(return_code) if return_code is not None else None,
            "stdout_line_count": min(len(raw_stdout.splitlines()), MAX_TRACE_LINES),
            "stderr_line_count": min(len(raw_stderr.splitlines()), MAX_TRACE_LINES),
            "stdout_bytes_observed": min(stdout_bytes, MAX_TRACE_STREAM_BYTES),
            "stderr_bytes_observed": min(stderr_bytes, MAX_TRACE_STREAM_BYTES),
            "stream_truncated": stdout_bytes > MAX_TRACE_STREAM_BYTES or stderr_bytes > MAX_TRACE_STREAM_BYTES,
            "json_event_count": sum(event_counts.values()),
            "event_counts": dict(sorted(event_counts.items())[:128]),
            "command_execution_count": command_execution,
            "completed_count": completed,
            "in_progress_count": in_progress,
            "agent_message_count": agent_message,
            "last_structural_event_type": last_type,
            "malformed_json_line_count": malformed,
        }

    def persist(self, trace: dict) -> bool:
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.root, 0o700)
            destination = self.root / f"{trace['execution_id']}.json"
            descriptor, temporary = tempfile.mkstemp(prefix=".trace-", dir=self.root)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(trace, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
                return True
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        except (OSError, ValueError, TypeError, KeyError):
            return False


class LocalWorktreeCodexTaskSpec(CodexTaskSpec):
    """Codex task bound to one explicit feature worktree instead of production AI_ROOT."""

    def validate(self) -> dict:
        root = validate_workspace(self.repo_root).root
        policy = RepoAccessPolicy(root)
        allowed = [str(path) for path in policy.validate_allowed_paths(list(self.allowed_paths))]
        generated_manifest = policy.build_safe_file_manifest(tuple(Path(path) for path in allowed))
        manifest = (
            policy.validate_supplied_manifest(
                self.safe_file_manifest,
                tuple(Path(path) for path in allowed),
                self.candidate_identity,
            )
            if self.safe_file_manifest
            else generated_manifest
        )
        write_policy = RepoWritePolicy(root, self.write_roots)
        write_roots = tuple(str(path.resolve()) for path in write_policy.write_roots)
        for path in write_roots:
            write_policy.validate_write_path(Path(path) / "contract.py")
        if not self.task_prompt or len(self.task_prompt.encode()) > MAX_WORK_UNIT_PROMPT_BYTES:
            raise ValueError("Codex task prompt outside safe size bound")
        if SecretFirewall().inspect(self.task_prompt).action == "BLOCK":
            raise ValueError("Codex task prompt rejected by Secret Firewall")
        if not 1 <= float(self.timeout_seconds) <= 3600:
            raise ValueError("Codex timeout outside safe range")
        if self.model_role != "CODE":
            raise ValueError("local Qwen producer requires CODE model role")
        schema = _safe_audit_value(self.expected_output_schema)
        _json_exact(schema, 16_000)
        return {
            "repo_root": str(root),
            "allowed_paths": allowed,
            "task_prompt_sha256": hashlib.sha256(self.task_prompt.encode()).hexdigest(),
            "risk_level": self.risk_level,
            "timeout_seconds": float(self.timeout_seconds),
            "model_role": self.model_role,
            "expected_output_schema": schema,
            "safe_file_manifest": list(manifest),
            "candidate_identity": self.candidate_identity.to_mapping() if self.candidate_identity else None,
            "write_roots": list(write_roots),
        }

    def execution_view(self) -> "LocalWorktreeCodexTaskSpec":
        validated = self.validate()
        file_paths = tuple(self.repo_root / item["path"] for item in validated["safe_file_manifest"])
        if not file_paths:
            raise PermissionError("safe execution manifest contains no files")
        return LocalWorktreeCodexTaskSpec(
            Path(validated["repo_root"]),
            file_paths,
            self.task_prompt,
            self.risk_level,
            self.timeout_seconds,
            self.model_role,
            self.expected_output_schema,
            tuple(validated["safe_file_manifest"]),
            self.candidate_identity,
            tuple(Path(path) for path in validated["write_roots"]),
        )

    def validate_write_path(self, value: Path | str) -> Path:
        validated = self.validate()
        return RepoWritePolicy(
            Path(validated["repo_root"]),
            tuple(Path(path) for path in validated["write_roots"]),
        ).validate_git_ownership(value)


class LocalWorktreeReviewTaskSpec(ReviewTaskSpec):
    """Read-only review task for the same explicit feature worktree."""

    def validate(self) -> dict:
        root = validate_workspace(self.repo_root).root
        if self.read_only is not True:
            raise PermissionError("review task must be read-only")
        if self.model_role != "REVIEW":
            raise ValueError("review task model_role must be REVIEW")
        policy = RepoAccessPolicy(root)
        allowed = [str(path) for path in policy.validate_allowed_paths(list(self.allowed_paths))]
        generated_manifest = policy.build_safe_file_manifest(tuple(Path(path) for path in allowed))
        manifest = (
            policy.validate_supplied_manifest(
                self.safe_file_manifest,
                tuple(Path(path) for path in allowed),
                self.candidate_identity,
            )
            if self.safe_file_manifest
            else generated_manifest
        )
        if not self.task_prompt or len(self.task_prompt.encode()) > 256_000:
            raise ValueError("review prompt outside safe size bound")
        if SecretFirewall().inspect(self.task_prompt).action == "BLOCK":
            raise ValueError("review prompt rejected by Secret Firewall")
        if not 1 <= float(self.timeout_seconds) <= 3600:
            raise ValueError("review timeout outside safe range")
        schema = recursive_private_sanitize(self.expected_review_schema)
        _json_exact(schema, 16_000)
        if _canonical_digest(schema) != _canonical_digest(REVIEW_RESULT_SCHEMA):
            raise ValueError("unsupported review result schema")
        objective_mapping = self.task_objective.to_mapping() if self.task_objective else None
        if objective_mapping is not None:
            objective_sha = hashlib.sha256(_json_exact(objective_mapping, 256_000).encode()).hexdigest()
            if self.objective_sha256 != objective_sha:
                raise ValueError("review objective content hash mismatch")
            if not self.objective_manifest_hash or not re.fullmatch(r"[a-f0-9]{64}", self.objective_manifest_hash):
                raise ValueError("review objective manifest hash missing")
        return {
            "repo_root": str(root),
            "allowed_paths": allowed,
            "read_only": True,
            "risk_level": self.risk_level,
            "timeout_seconds": float(self.timeout_seconds),
            "model_role": "REVIEW",
            "expected_review_schema": schema,
            "task_prompt_sha256": hashlib.sha256(self.task_prompt.encode()).hexdigest(),
            "safe_file_manifest": list(manifest),
            "task_objective": objective_mapping,
            "objective_sha256": self.objective_sha256,
            "objective_manifest_hash": self.objective_manifest_hash,
        }

    def execution_view(self) -> "LocalWorktreeReviewTaskSpec":
        validated = self.validate()
        file_paths = tuple(self.repo_root / item["path"] for item in validated["safe_file_manifest"])
        if not file_paths:
            raise PermissionError("safe reviewer manifest contains no files")
        return LocalWorktreeReviewTaskSpec(
            Path(validated["repo_root"]),
            file_paths,
            self.task_prompt,
            self.read_only,
            self.risk_level,
            self.timeout_seconds,
            self.model_role,
            self.expected_review_schema,
            tuple(validated["safe_file_manifest"]),
            self.candidate_identity,
            self.task_objective,
            self.objective_sha256,
            self.objective_manifest_hash,
        )


class LocalWorktreeSupervisorRepository(Round2SupervisorRepository):
    """Separate durable Supervisor state bound to one approved feature worktree."""

    def __init__(self, repo_root: Path, path: Path = LOCAL_QWEN_SUPERVISOR_DB):
        evidence = validate_workspace(repo_root)
        self.repo_root = evidence.root
        from .supervisor_contracts import CandidateIdentityProvider

        provider = CandidateIdentityProvider(self.repo_root)
        super().__init__(Path(path), candidate_identity_provider=provider)

    def create_job(
        self,
        title: str,
        owner_id: str,
        project_scope: str | None = None,
        risk_level: str = "LOW",
        created_by: str = "owner",
        metadata=None,
        max_review_rounds: int = 2,
        max_attempts_per_stage: int = 2,
        job_id: str | None = None,
        mutation_capable: bool = True,
    ) -> WorkflowJob:
        evidence = validate_workspace(self.repo_root)
        resolved = Path(project_scope or evidence.root).resolve()
        if resolved != evidence.root:
            raise PermissionError("project_scope must equal the approved local feature worktree")
        if SecretFirewall().inspect(title).action == "BLOCK":
            raise ValueError("job title rejected by Secret Firewall")
        if not 1 <= max_review_rounds <= 5 or not 1 <= max_attempts_per_stage <= 5:
            raise ValueError("round/attempt limit outside safe range")
        if not mutation_capable:
            raise ValueError("READ_ONLY_PROBE_IS_NOT_A_WORKFLOW_JOB")
        supplied = dict(metadata or {})
        if {"baseline_commit_sha", "candidate_base_commit_sha"} & set(supplied):
            raise ValueError("trusted baseline cannot be supplied through metadata")
        prepared = recursive_private_sanitize(supplied)
        metadata_json = _safe_json(prepared, 16_000)
        now, identifier = utc_now(), job_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,46}", identifier):
            raise ValueError("job_id is not callback-safe")
        request_hash = self._job_request_hash(
            identifier,
            owner_id,
            title,
            str(resolved),
            risk_level,
            created_by,
            max_review_rounds,
            max_attempts_per_stage,
            json.loads(metadata_json),
            True,
        )
        if job_id:
            existing = self.db.execute("SELECT * FROM supervisor_jobs WHERE job_id=?", (job_id,)).fetchone()
            if existing:
                if existing["job_request_hash"] != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return self._job_from_row(existing)
        baseline = self.candidate_identity_provider.capture_baseline()
        baseline_identity = self.candidate_identity_provider.snapshot(baseline)
        if not self.candidate_identity_provider.worktree_is_clean():
            raise RuntimeError("WORKTREE_NOT_CLEAN")
        if self.candidate_identity_provider.unowned_write_root_paths():
            raise RuntimeError("WORKTREE_WRITE_ROOT_NOT_OWNABLE")
        baseline_state = hashlib.sha256(
            _json_exact(baseline_identity.stable_payload(), 1_000_000).encode()
        ).hexdigest()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            active = self.db.execute(
                "SELECT COUNT(*) FROM supervisor_jobs WHERE mutation_capable=1 AND status IN (?,?,?,?)",
                (
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                    JobStatus.WAITING.value,
                    JobStatus.BLOCKED.value,
                ),
            ).fetchone()[0]
            if active >= MAX_MUTATING_JOBS_IN_SYSTEM:
                self.db.rollback()
                raise RuntimeError("MAX_MUTATING_JOBS_IN_SYSTEM=1")
            self.db.execute(
                "INSERT INTO supervisor_jobs "
                "(job_id,title,project_scope,created_at,updated_at,owner_id,risk_level,status,current_stage,attempt,"
                "review_round,max_review_rounds,max_attempts_per_stage,last_error,resume_state,created_by,"
                "metadata_json,next_retry_at,baseline_commit_sha,mutation_capable,baseline_candidate_state_sha256,"
                "job_request_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    _bounded(title, 200),
                    str(resolved),
                    now,
                    now,
                    str(owner_id),
                    risk_level,
                    JobStatus.QUEUED.value,
                    WorkflowStage.INTAKE.value,
                    0,
                    0,
                    max_review_rounds,
                    max_attempts_per_stage,
                    None,
                    None,
                    created_by,
                    metadata_json,
                    None,
                    baseline,
                    1,
                    baseline_state,
                    request_hash,
                ),
            )
            self.record_event(
                identifier,
                "JOB_CREATED",
                WorkflowStage.INTAKE,
                {"risk_level": risk_level, "local_qwen": True},
                commit=False,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.get_job(identifier)

    def create_work_unit(self, job_id, owner_id, stage, spec, *args, **kwargs):
        job = self.get_job_for_owner(job_id, owner_id)
        if not isinstance(spec, LocalWorktreeCodexTaskSpec):
            raise TypeError("local Supervisor requires LocalWorktreeCodexTaskSpec")
        if Path(spec.repo_root).resolve() != Path(job.project_scope).resolve():
            raise PermissionError("work unit repository does not match durable job scope")
        return super().create_work_unit(job_id, owner_id, stage, spec, *args, **kwargs)

    def reconstruct_codex_task(self, job_id, owner_id, stage, review_round=None):
        unit = self.work_unit_for_stage(job_id, owner_id, stage, review_round)
        job = self.get_job_for_owner(job_id, owner_id)
        if unit.repo_root.resolve() != Path(job.project_scope).resolve():
            raise PermissionError("durable work unit repository scope mismatch")
        prompt = self.load_work_unit_prompt(unit.work_unit_id, job_id, owner_id)
        spec = LocalWorktreeCodexTaskSpec(
            unit.repo_root,
            unit.allowed_paths,
            prompt,
            unit.risk_level,
            unit.timeout_seconds,
            unit.model_role,
            unit.expected_output_schema,
            unit.safe_file_manifest,
            unit.candidate_identity,
            unit.write_roots,
        )
        validated = spec.validate()
        if validated["task_prompt_sha256"] != unit.prompt_sha256:
            raise ValueError("work unit prompt hash mismatch")
        return spec

    def create_review_work_unit(self, job_id, owner_id, review_round, spec, *args, **kwargs):
        job = self.get_job_for_owner(job_id, owner_id)
        if not isinstance(spec, LocalWorktreeReviewTaskSpec):
            raise TypeError("local Supervisor requires LocalWorktreeReviewTaskSpec")
        if Path(spec.repo_root).resolve() != Path(job.project_scope).resolve():
            raise PermissionError("review work unit repository does not match durable job scope")
        return super().create_review_work_unit(job_id, owner_id, review_round, spec, *args, **kwargs)

    def reconstruct_reviewer_task(self, job_id, owner_id, review_round):
        unit = self.review_work_unit_for_round(job_id, owner_id, review_round)
        job = self.get_job_for_owner(job_id, owner_id)
        if unit.repo_root.resolve() != Path(job.project_scope).resolve():
            raise PermissionError("durable review repository scope mismatch")
        prompt = self.content_store.get(unit.prompt_content_ref, unit.prompt_sha256)
        objective_payload = self.content_store.get(unit.objective_content_ref, unit.objective_sha256)
        objective = TaskObjective.from_mapping(json.loads(objective_payload))
        spec = LocalWorktreeReviewTaskSpec(
            unit.repo_root,
            unit.allowed_paths,
            prompt,
            unit.read_only,
            unit.risk_level,
            unit.timeout_seconds,
            unit.model_role,
            unit.expected_review_schema,
            unit.safe_file_manifest,
            unit.candidate_identity,
            objective,
            unit.objective_sha256,
            unit.objective_manifest_hash,
        )
        validated = spec.validate()
        if validated["task_prompt_sha256"] != unit.prompt_sha256:
            raise ValueError("review work unit prompt hash mismatch")
        return spec


class LocalQwenCodexRunner:
    """Explicitly enabled local Qwen planner using Codex as the execution shell."""

    cancellation_supported = True

    def __init__(
        self,
        *,
        enabled: bool = False,
        health_probe=None,
        popen_factory=subprocess.Popen,
        bridge_health_url: str = DEFAULT_BRIDGE_HEALTH_URL,
        trace_root: Path = LOCAL_QWEN_EXECUTION_TRACE_ROOT,
        pgid_resolver=os.getpgid,
        group_signaler=os.killpg,
        cancel_wait_seconds: float = 5.0,
    ):
        self.enabled = enabled is True
        self.health_probe = health_probe or self._probe_bridge
        self.popen_factory = popen_factory
        self.bridge_health_url = bridge_health_url
        self.trace_store = LocalQwenExecutionTrace(trace_root)
        self.pgid_resolver = pgid_resolver
        self.group_signaler = group_signaler
        self.cancel_wait_seconds = max(0.05,min(float(cancel_wait_seconds),30.0))
        self._executions: dict[str, tuple[object,int,int]] = {}
        self._canceling: set[str] = set()
        self._execution_lock = threading.RLock()

    def cancel(self, execution_id=None, reason=None) -> bool:
        try:
            identifier=str(uuid.UUID(str(execution_id)))
        except (ValueError,AttributeError,TypeError):
            return False
        with self._execution_lock:
            owned=self._executions.get(identifier)
            if owned is not None and identifier in self._canceling:
                return False
            if owned is not None:
                self._canceling.add(identifier)
        if owned is None:
            return False
        process,pid,pgid=owned
        if process.pid != pid or process.poll() is not None:
            with self._execution_lock:
                self._canceling.discard(identifier)
                if self._executions.get(identifier) is owned:
                    self._executions.pop(identifier,None)
            return False
        try:
            if self.pgid_resolver(pid) != pgid:
                return False
            self.group_signaler(pgid,signal.SIGTERM)
            try:
                process.wait(timeout=self.cancel_wait_seconds)
            except subprocess.TimeoutExpired:
                if process.poll() is not None or self.pgid_resolver(pid) != pgid:
                    process.wait(timeout=self.cancel_wait_seconds)
                else:
                    self.group_signaler(pgid,signal.SIGKILL)
                    process.wait(timeout=self.cancel_wait_seconds)
        except (OSError,subprocess.SubprocessError):
            return False
        finally:
            with self._execution_lock:
                self._canceling.discard(identifier)
                if process.poll() is not None:
                    if self._executions.get(identifier) is owned:
                        self._executions.pop(identifier,None)
        return process.poll() is not None

    def _register(self,execution_id: str,process) -> tuple[object,int,int]:
        pid=int(process.pid)
        pgid=int(self.pgid_resolver(pid))
        owned=(process,pid,pgid)
        with self._execution_lock:
            if execution_id in self._executions:
                raise RuntimeError("LOCAL_QWEN_EXECUTION_ALREADY_ACTIVE")
            self._executions[execution_id]=owned
        return owned

    def _unregister(self,execution_id: str,owned) -> None:
        with self._execution_lock:
            if self._executions.get(execution_id) is owned:
                self._executions.pop(execution_id,None)

    @staticmethod
    def _reap_spawned_child(process) -> bool:
        """Stop only the exact Popen child when ownership registration failed."""
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
            else:
                process.wait(timeout=5)
            return process.poll() is not None
        except (OSError,subprocess.SubprocessError):
            return False

    def _probe_bridge(self):
        request = urllib.request.Request(self.bridge_health_url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise RuntimeError("local Qwen bridge unavailable") from error

    @staticmethod
    def _safe_env() -> dict[str, str]:
        env = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
        env["NO_COLOR"] = "1"
        return env

    @staticmethod
    def _health_valid(health) -> bool:
        return bool(
            isinstance(health, dict)
            and health.get("status") == "healthy"
            and health.get("backend") == QWEN38.model_id
            and health.get("tool") == "exec_command"
        )

    def run_task(self, spec: CodexTaskSpec, execution_id: str) -> StageResult:
        try:
            uuid.UUID(str(execution_id))
        except (ValueError, AttributeError) as error:
            raise ValueError("execution_id must be a canonical UUID") from error
        if not self.enabled:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Local Qwen Producer is disabled until explicitly enabled",
                error="LOCAL_QWEN_PRODUCER_DISABLED",
                metrics={"provider": "local-qwen-codex"},
            )
        if not isinstance(spec, LocalWorktreeCodexTaskSpec):
            return StageResult(
                StageResultStatus.BLOCKED,
                "Local Qwen Producer requires a feature-worktree task contract",
                error="LOCAL_QWEN_TASK_CONTRACT_DENIED",
            )
        try:
            validated = spec.validate()
            root = validate_workspace(validated["repo_root"]).root
        except (WorkspacePolicyError, PermissionError, ValueError, OSError) as error:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Local Qwen Producer workspace policy denied execution",
                error="LOCAL_QWEN_WORKSPACE_DENIED",
                metrics={"category": type(error).__name__},
            )
        try:
            health = self.health_probe()
        except Exception as error:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Local Qwen bridge health is unavailable",
                error="LOCAL_QWEN_BRIDGE_UNAVAILABLE",
                metrics={"category": type(error).__name__},
            )
        if not self._health_valid(health):
            return StageResult(
                StageResultStatus.BLOCKED,
                "Local Qwen bridge identity or health did not match V1 qualification",
                error="LOCAL_QWEN_BRIDGE_IDENTITY_MISMATCH",
            )
        launcher = root / "control-plane/scripts/run-codex-qwen-local.sh"
        try:
            resolved_launcher = launcher.resolve(strict=True)
        except OSError:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Qualified Local Qwen launcher is missing",
                error="LOCAL_QWEN_LAUNCHER_MISSING",
            )
        if resolved_launcher != launcher or launcher.is_symlink() or not launcher.is_file():
            return StageResult(
                StageResultStatus.BLOCKED,
                "Qualified Local Qwen launcher path is unsafe",
                error="LOCAL_QWEN_LAUNCHER_DENIED",
            )
        command = (
            "/bin/zsh",
            str(launcher),
            str(root),
            "exec",
            "--json",
            "--ephemeral",
            spec.task_prompt,
        )
        started = time.monotonic()
        started_at = utc_now()
        process=None
        owned=None
        try:
            process = self.popen_factory(
                command,
                cwd=root,
                env=self._safe_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                start_new_session=True,
            )
            owned=self._register(execution_id,process)
            stdout,stderr=process.communicate(timeout=float(spec.timeout_seconds))
        except subprocess.TimeoutExpired as error:
            terminated=self.cancel(execution_id,reason="TIMEOUT")
            terminated=terminated or (process is not None and process.poll() is not None)
            if terminated and process is not None:
                try:
                    stdout,stderr=process.communicate(timeout=self.cancel_wait_seconds)
                except (OSError,subprocess.SubprocessError):
                    stdout,stderr=error.stdout,error.stderr
            else:
                stdout,stderr=error.stdout,error.stderr
            duration = round(time.monotonic() - started, 3)
            trace=self.trace_store.summarize(
                execution_id,started_at=started_at,ended_at=utc_now(),duration_seconds=duration,
                timed_out=True,return_code=getattr(process,"returncode",None),stdout=stdout,stderr=stderr,
            )
            self.trace_store.persist(trace)
            category="LOCAL_QWEN_CODEX_TIMEOUT_TERMINATED" if terminated else "LOCAL_QWEN_CODEX_TIMEOUT_ACTIVE_UNCERTAIN"
            raise LocalProducerExecutionUncertain(category) from error
        except OSError as error:
            if process is not None and owned is None and not self._reap_spawned_child(process):
                raise LocalProducerExecutionUncertain("LOCAL_QWEN_LAUNCH_CHILD_UNREAPED") from error
            return StageResult(
                StageResultStatus.BLOCKED,
                "Codex local launcher could not start",
                error="LOCAL_QWEN_LAUNCH_FAILED",
                metrics={"category": type(error).__name__},
            )
        except Exception as error:
            if process is not None and owned is None:
                self._reap_spawned_child(process)
            elif process is not None and process.poll() is None:
                self.cancel(execution_id,reason="RUNNER_EXCEPTION")
            raise LocalProducerExecutionUncertain(type(error).__name__) from error
        finally:
            if owned is not None and process is not None and process.poll() is not None:
                self._unregister(execution_id,owned)
        duration = round(time.monotonic() - started, 3)
        return_code = int(getattr(process, "returncode", -1))
        trace=self.trace_store.summarize(
            execution_id,started_at=started_at,ended_at=utc_now(),duration_seconds=duration,
            timed_out=False,return_code=return_code,stdout=stdout,stderr=stderr,
        )
        trace_written=self.trace_store.persist(trace)
        metrics = {
            "provider": "local-qwen-codex",
            "duration_seconds": duration,
            "return_code": return_code,
            "network_access": False,
            "git_mutation_authority": False,
            "trace_written": trace_written,
            "json_event_count": trace["json_event_count"],
            "command_execution_count": trace["command_execution_count"],
        }
        if return_code == 0:
            return StageResult.passed("Local Qwen Producer completed through Codex CLI", metrics=metrics)
        return StageResult.failed(
            "Local Qwen Producer Codex process exited without success",
            error=f"LOCAL_QWEN_CODEX_EXIT_{return_code}",
            metrics=metrics,
        )


class LocalWorktreeValidationRunner:
    def __init__(self, repo_root: Path, argv=None, timeout_seconds: float = 120):
        self.repo_root = validate_workspace(repo_root).root
        self.cwd = self.repo_root / "control-plane"
        self.argv = tuple(argv or (str(CONTROL_PLANE_PYTHON), "-m", "pytest", "-q"))
        self.timeout_seconds = timeout_seconds
        self.policy = SafeCommandPolicy(python=CONTROL_PLANE_PYTHON, cwd_root=self.cwd)

    def run(self, context: StageContext) -> StageResult:
        if Path(context.job.project_scope).resolve() != self.repo_root:
            return StageResult(StageResultStatus.BLOCKED, "Validation scope denied", error="PATH_SCOPE")
        try:
            command = self.policy.validate(self.argv, self.cwd)
        except (PermissionError, ValueError) as error:
            return StageResult(StageResultStatus.BLOCKED, "Validation command denied", error=str(error))
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                shell=False,
                timeout=min(self.timeout_seconds, context.timeout_seconds),
                check=False,
                env={
                    "PATH": os.defpath,
                    "PYTHONPATH": str(self.cwd / "src"),
                    "PYTHONHASHSEED": "0",
                },
            )
        except subprocess.TimeoutExpired:
            return StageResult(
                StageResultStatus.TIMEOUT,
                "Validation timed out",
                error="TIMEOUT",
                metrics={"duration_seconds": round(time.monotonic() - started, 3)},
            )
        duration = round(time.monotonic() - started, 3)
        combined = (completed.stdout + "\n" + completed.stderr).strip()
        if SecretFirewall().inspect(combined).action == "BLOCK":
            summary = "validation output redacted by Secret Firewall"
        else:
            summary = _bounded(combined, 4096) or "validation produced no output"
        metrics = {
            "return_code": completed.returncode,
            "duration_seconds": duration,
            "stdout_chars": len(completed.stdout),
            "stderr_chars": len(completed.stderr),
        }
        if completed.returncode == 0:
            return StageResult.passed(summary, metrics=metrics)
        return StageResult.failed(summary, error=f"pytest_exit_{completed.returncode}", metrics=metrics)


class LocalWorktreeSecurityRunner(Round2SecurityRunner):
    def _run_isolation_regression(self, context: StageContext) -> StageResult:
        return LocalWorktreeValidationRunner(
            self.repo_root,
            (
                str(CONTROL_PLANE_PYTHON),
                "-m",
                "pytest",
                "-q",
                "tests/test_gateway_v02.py",
                "tests/test_control.py",
            ),
            timeout_seconds=60,
        ).run(context)

    def run(self, context: StageContext) -> StageResult:
        if self.repo_root != Path(context.job.project_scope).resolve():
            return StageResult(StageResultStatus.BLOCKED, "Security scope denied", error="PATH_SCOPE")
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
            check=False,
        )
        if tracked.returncode != 0:
            return StageResult.failed("Unable to enumerate tracked files", error="GIT_LS_FILES")
        forbidden = [line for line in tracked.stdout.splitlines() if self.forbidden_tracked.search(line)]
        if forbidden:
            return StageResult.failed(
                "Tracked runtime/secret policy failed",
                error="FORBIDDEN_TRACKED_FILE",
                metrics=self._base_metrics(files_blocked=len(forbidden)),
            )
        changed = subprocess.run(
            ["git", "diff", "--name-status", "-z", "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
            check=False,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
            check=False,
        )
        if changed.returncode != 0 or untracked.returncode != 0:
            return StageResult.failed("Unable to enumerate candidate files", error="GIT_CANDIDATE_FILES")
        try:
            scan_paths, deletions, renames = self._parse_changed(changed.stdout)
        except ValueError:
            return StageResult.failed("Unable to parse candidate file status", error="GIT_CANDIDATE_FILES")
        for relative in deletions:
            candidate = (self.repo_root / relative).resolve()
            if not candidate.is_relative_to(self.repo_root) or not self._tracked_in_head(relative):
                return StageResult.failed(
                    "Deletion path is not a tracked in-scope file",
                    error="UNSCANNABLE_CANDIDATE",
                )
        scan = self._scan_candidates(scan_paths + [x for x in untracked.stdout.split("\0") if x])
        if scan.status is not StageResultStatus.PASS:
            scan.metrics.update({"deleted": len(deletions), "renamed": len(renames)})
            return scan
        metrics = scan.metrics | {"deleted": len(deletions), "renamed": len(renames)}
        isolation = self._run_isolation_regression(context)
        if isolation.status is not StageResultStatus.PASS:
            return StageResult.failed(
                "Security isolation regression failed",
                error="SECURITY_REGRESSION",
                metrics=metrics | {"isolation_return_code": isolation.metrics.get("return_code")},
            )
        return StageResult.passed(
            "Security policies and isolation regressions passed",
            metrics=metrics
            | {"forbidden_count": 0, "isolation_return_code": isolation.metrics.get("return_code")},
        )


class LocalWorktreeGitGateRunner(GitGateRunner):
    def run(self, context: StageContext) -> StageResult:
        if self.repo_root != Path(context.job.project_scope).resolve():
            return StageResult(StageResultStatus.BLOCKED, "Git Gate scope denied", error="PATH_SCOPE")
        completed = {
            row["stage"]
            for row in context.repository.db.execute(
                "SELECT stage FROM supervisor_stage_runs WHERE job_id=? AND status='PASS'",
                (context.job.job_id,),
            ).fetchall()
        }
        required = {
            WorkflowStage.VALIDATION.value,
            WorkflowStage.REVIEW.value,
            WorkflowStage.SECURITY.value,
        }
        missing = sorted(required - completed)
        if missing:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Git Gate prerequisites are incomplete",
                error="GIT_GATE_PREREQUISITES",
                metrics={"missing_stages": missing},
            )
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            shell=False,
            timeout=5,
            check=False,
        ).stdout.strip()
        if not branch or branch in {"main", "master"}:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Git Gate requires a feature branch",
                error="MAIN_BRANCH_DENIED",
            )
        return StageResult.passed(
            "Git Gate policy satisfied; no Git mutation performed",
            metrics={"branch": branch, "git_mutation": False, "review_pending": True},
        )


class LocalWorktreeDurableReviewRunner(DurableReviewRunner):
    def run(self, context: StageContext) -> StageResult:
        if context.stage is not WorkflowStage.REVIEW:
            return StageResult(StageResultStatus.BLOCKED, "review runner scope denied", error="REVIEW_STAGE_SCOPE")
        round_number = context.job.review_round + 1
        try:
            unit = context.repository.review_work_unit_for_round(
                context.job.job_id, context.job.owner_id, round_number
            )
            context.repository.reconstruct_reviewer_task(
                context.job.job_id, context.job.owner_id, round_number
            )
            result = context.repository.submitted_review_result(
                context.job.job_id,
                context.job.owner_id,
                round_number,
                unit.review_work_unit_id,
            )
        except KeyError:
            return StageResult(
                StageResultStatus.BLOCKED,
                "durable review result pending",
                error="REVIEW_RESULT_PENDING",
            )
        return result.to_stage_result(Path(context.job.project_scope))


class LocalWorktreeWorkflowSupervisor(Round2WorkflowSupervisor):
    def _default_review_spec(self, job: WorkflowJob, review_round: int) -> LocalWorktreeReviewTaskSpec:
        root = validate_workspace(job.project_scope).root
        prompt = (
            f"Independently review workflow job {job.job_id}, review round {review_round}. "
            "Read only within the allowed repository paths. Return only the expected structured review schema."
        )
        return LocalWorktreeReviewTaskSpec(
            root,
            (root / "control-plane", root / "docs"),
            prompt,
            True,
            job.risk_level,
            min(float(self.timeout_seconds), 3600.0),
            "REVIEW",
            REVIEW_RESULT_SCHEMA,
        )


def local_qwen_runners(repo_root: Path, *, enabled: bool = False):
    root = validate_workspace(repo_root).root
    return {
        WorkflowStage.INTAKE: StaticPassRunner("Intake schema validated"),
        WorkflowStage.PRODUCER: PersistedCodexStageRunner(LocalQwenCodexRunner(enabled=enabled)),
        WorkflowStage.VALIDATION: LocalWorktreeValidationRunner(root),
        WorkflowStage.SELF_ACCEPTANCE: StaticPassRunner("Deterministic self acceptance passed"),
        WorkflowStage.REVIEW: LocalWorktreeDurableReviewRunner(),
        WorkflowStage.REVISION: PersistedCodexStageRunner(LocalQwenCodexRunner(enabled=enabled)),
        WorkflowStage.SECURITY: LocalWorktreeSecurityRunner(root),
        WorkflowStage.GIT_GATE: LocalWorktreeGitGateRunner(root),
    }


def create_local_qwen_job(
    repository: LocalWorktreeSupervisorRepository,
    *,
    title: str,
    owner_id: str,
    task_prompt: str,
    risk_level: str = "LOW",
    timeout_seconds: float = 900,
    expected_output_schema: dict | None = None,
    job_id: str | None = None,
):
    job = repository.create_job(
        title,
        owner_id,
        project_scope=str(repository.repo_root),
        risk_level=risk_level,
        job_id=job_id,
    )
    spec = LocalWorktreeCodexTaskSpec(
        repository.repo_root,
        (repository.repo_root / "control-plane", repository.repo_root / "docs"),
        task_prompt,
        risk_level,
        timeout_seconds,
        "CODE",
        expected_output_schema or {"type": "object"},
    )
    unit = repository.create_work_unit(
        job.job_id,
        owner_id,
        WorkflowStage.PRODUCER,
        spec,
        work_unit_id=f"producer-{job.job_id}",
        review_round=0,
    )
    return job, unit
