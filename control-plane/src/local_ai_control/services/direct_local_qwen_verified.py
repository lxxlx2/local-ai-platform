from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from local_ai_control.services.qwen38_runtime import Qwen38Provider, RuntimeUnavailable

from .codex_qwen_workspace import WorkspacePolicyError, validate_workspace
from .direct_local_qwen_agent import (
    DirectGenericProjectQwenRunner,
    DirectLocalQwenAgent,
    DirectLocalQwenProtocolError,
    DirectProjectToolbox,
    _bounded,
    parse_direct_agent_action,
)
from .generic_project_policy import TestProfile
from .supervisor_contracts import StageResult, StageResultStatus
from .supervisor_generic_project import GenericProjectCodexTaskSpec


MAX_FINALIZATION_DENIALS = 6


class VerifiedDirectProjectToolbox(DirectProjectToolbox):
    """Direct toolbox with deterministic evidence required before FINAL."""

    def __init__(self, repo_root: Path, test_profile: TestProfile = TestProfile.NONE):
        super().__init__(repo_root, test_profile)
        self.write_generation = 0
        self.successful_writes = 0
        self.last_diff_generation: int | None = None
        self.last_diff_nonempty = False
        self.last_test_generation: int | None = None
        self.last_test_return_code: int | None = None

    def _candidate_diff(self) -> str:
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(self.repo_root), "diff", "--no-ext-diff", "--", "."],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            shell=False,
            timeout=20,
            check=False,
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"},
        )
        if completed.returncode != 0:
            raise RuntimeError("git diff failed")
        return completed.stdout

    def write_file(self, path: str, content: str) -> str:
        result = super().write_file(path, content)
        self.write_generation += 1
        self.successful_writes += 1
        return result

    def git_diff(self) -> str:
        result = super().git_diff()
        raw = self._candidate_diff()
        blocked = self.firewall.inspect(raw).action == "BLOCK"
        self.last_diff_generation = self.write_generation
        self.last_diff_nonempty = bool(raw.strip()) and not blocked
        return result

    def run_tests(self) -> str:
        result = super().run_tests()
        if self.test_profile is not TestProfile.NONE:
            payload = json.loads(result)
            code = payload.get("return_code")
            if isinstance(code, int):
                self.last_test_generation = self.write_generation
                self.last_test_return_code = code
        return result

    def ensure_controller_postconditions(self) -> tuple[str, ...]:
        """Run deterministic postconditions after a mutation without model discretion.

        The model may choose git_diff/run_tests itself, but FINAL correctness must
        not depend on the model remembering those fixed, owner-approved checks.
        The controller therefore performs any missing diff inspection and selected
        fixed test profile after the latest successful write.
        """
        if self.successful_writes < 1:
            return ()
        evidence: list[str] = []
        if self.last_diff_generation != self.write_generation or not self.last_diff_nonempty:
            evidence.append("git_diff=" + _bounded(self.git_diff(), 4000))
        if self.test_profile is not TestProfile.NONE and self.last_test_generation != self.write_generation:
            evidence.append("run_tests=" + _bounded(self.run_tests(), 5000))
        return tuple(evidence)

    def finalization_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.successful_writes < 1:
            reasons.append("no successful write_file call")

        raw = self._candidate_diff()
        if not raw.strip():
            reasons.append("candidate Git diff is empty")
        elif self.firewall.inspect(raw).action == "BLOCK":
            reasons.append("candidate Git diff is blocked by Secret Firewall")

        if self.last_diff_generation != self.write_generation or not self.last_diff_nonempty:
            reasons.append("git_diff was not successfully inspected after the latest write")

        if self.test_profile is not TestProfile.NONE:
            if self.last_test_generation != self.write_generation or self.last_test_return_code != 0:
                reasons.append("fixed tests have not passed after the latest write")

        return tuple(dict.fromkeys(reasons))

    def finalization_metrics(self) -> dict[str, Any]:
        return {
            "successful_writes": self.successful_writes,
            "write_generation": self.write_generation,
            "diff_verified_after_latest_write": (
                self.last_diff_generation == self.write_generation and self.last_diff_nonempty
            ),
            "tests_verified_after_latest_write": (
                self.test_profile is TestProfile.NONE
                or (
                    self.last_test_generation == self.write_generation
                    and self.last_test_return_code == 0
                )
            ),
            "candidate_diff_nonempty": bool(self._candidate_diff().strip()),
        }


class VerifiedDirectLocalQwenAgent(DirectLocalQwenAgent):
    """Reject premature FINAL until deterministic mutation evidence exists."""

    def run(self, objective: str, toolbox: VerifiedDirectProjectToolbox) -> tuple[str, dict[str, Any]]:
        transcript: list[str] = []
        malformed = 0
        tool_calls = 0
        finalization_denials = 0

        for step in range(1, self.max_steps + 1):
            reply = self.provider.generate(self._prompt(objective, transcript), max_output_tokens=1536)
            if not reply.complete or not reply.text:
                raise RuntimeUnavailable("local Qwen direct-agent generation incomplete")
            try:
                action = parse_direct_agent_action(reply.text)
            except DirectLocalQwenProtocolError as error:
                malformed += 1
                if malformed >= 3:
                    raise
                transcript.append("protocol_error: " + str(error))
                continue

            if action.kind == "FINAL":
                controller_evidence = toolbox.ensure_controller_postconditions()
                reasons = toolbox.finalization_reasons()
                if reasons:
                    finalization_denials += 1
                    if controller_evidence:
                        transcript.append(
                            "controller_finalization_evidence:\n" + "\n".join(controller_evidence)
                        )
                    transcript.append(
                        "finalization_denied: "
                        + json.dumps(
                            {
                                "reasons": list(reasons),
                                "required_next_action": "continue using allowlisted tools until all reasons are resolved",
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                    if finalization_denials >= MAX_FINALIZATION_DENIALS:
                        raise DirectLocalQwenProtocolError(
                            "direct agent repeatedly attempted FINAL without verified candidate evidence"
                        )
                    continue

                metrics = {
                    "agent_steps": step,
                    "tool_calls": tool_calls,
                    "malformed_actions": malformed,
                    "finalization_denials": finalization_denials,
                    "finalization_verified": True,
                    "controller_postconditions_enforced": True,
                    "executor": "direct-local-qwen-verified",
                    "codex_cli_invoked": False,
                    "network_access": False,
                }
                metrics.update(toolbox.finalization_metrics())
                return str(action.payload), metrics

            tool_calls += 1
            payload = dict(action.payload)
            try:
                result = toolbox.call(payload)
                transcript.append(
                    "tool_request: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    + "\ntool_result: " + _bounded(result)
                )
            except Exception as error:
                transcript.append(
                    "tool_request: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    + f"\ntool_error: {type(error).__name__}: {str(error)[:500]}"
                )

        raise DirectLocalQwenProtocolError("direct agent step limit reached")


class VerifiedDirectGenericProjectQwenRunner(DirectGenericProjectQwenRunner):
    """Generic runner that cannot PASS with an empty or unverified candidate."""

    @staticmethod
    def _safe_error_detail(error: Exception) -> str:
        return str(error).replace("\n", " ")[:500]

    def run_task(self, spec, execution_id: str) -> StageResult:
        if not self.enabled:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Direct Local Qwen executor is disabled until explicitly enabled",
                error="LOCAL_QWEN_PRODUCER_DISABLED",
            )
        if not isinstance(spec, GenericProjectCodexTaskSpec):
            return StageResult(
                StageResultStatus.BLOCKED,
                "Direct Local Qwen requires a generic project task contract",
                error="GENERIC_PROJECT_TASK_CONTRACT_DENIED",
            )
        try:
            validated = spec.validate()
            root = validate_workspace(validated["repo_root"]).root
            health = self.provider.health()
        except (WorkspacePolicyError, PermissionError, ValueError, OSError, RuntimeUnavailable) as error:
            return StageResult(
                StageResultStatus.BLOCKED,
                "Direct Local Qwen preflight failed",
                error="DIRECT_LOCAL_QWEN_PREFLIGHT_FAILED",
                metrics={"category": type(error).__name__, "detail": self._safe_error_detail(error)},
            )
        if not isinstance(health, dict) or health.get("status") != "healthy":
            return StageResult(
                StageResultStatus.BLOCKED,
                "Local Qwen sidecar is unhealthy",
                error="LOCAL_QWEN_HEALTH_MISMATCH",
            )

        toolbox = VerifiedDirectProjectToolbox(root, self.test_profile)
        try:
            summary, metrics = VerifiedDirectLocalQwenAgent(self.provider).run(spec.task_prompt, toolbox)
            toolbox.ensure_controller_postconditions()
            remaining = toolbox.finalization_reasons()
            if remaining:
                raise DirectLocalQwenProtocolError(
                    "verified finalization invariant changed after agent completion: " + "; ".join(remaining)
                )
        except Exception as error:
            detail = self._safe_error_detail(error)
            evidence_metrics: dict[str, Any] = {}
            evidence_error: Exception | None = None
            try:
                toolbox.ensure_controller_postconditions()
                remaining = toolbox.finalization_reasons()
                evidence_metrics = toolbox.finalization_metrics()
            except Exception as finalization_error:
                remaining = ("deterministic finalization evidence could not be read",)
                evidence_error = finalization_error

            if not remaining and toolbox.successful_writes > 0:
                metrics = {
                    "category": type(error).__name__,
                    "detail": detail,
                    "finalization_verified": True,
                    "finalization_salvaged_after_agent_error": True,
                    "controller_postconditions_enforced": True,
                    "agent_protocol_completion": "SYNTHETIC_FROM_DETERMINISTIC_EVIDENCE",
                    "codex_cli_invoked": False,
                    "network_access": False,
                }
                metrics.update(evidence_metrics)
                return StageResult.passed(
                    "Direct Local Qwen candidate passed deterministic finalization after agent protocol failure",
                    metrics=metrics,
                )

            metrics = {
                "category": type(error).__name__,
                "detail": detail,
                "codex_cli_invoked": False,
                "network_access": False,
                "finalization_verified": False,
                "controller_postconditions_enforced": True,
                "finalization_reasons": list(remaining),
            }
            metrics.update(evidence_metrics)
            if evidence_error is not None:
                metrics["finalization_evidence_error"] = (
                    f"{type(evidence_error).__name__}: {self._safe_error_detail(evidence_error)}"
                )
            return StageResult.failed(
                "Direct Local Qwen agent did not produce a verified candidate",
                error=f"DIRECT_LOCAL_QWEN_{type(error).__name__}",
                metrics=metrics,
            )
        return StageResult.passed(summary, metrics=metrics)
