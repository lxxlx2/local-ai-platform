# Local Producer V2: Supervisor integration

Status: FEATURE BRANCH / DISABLED BY DEFAULT / REVIEW PENDING

Branch: `feat/codex-qwen-supervisor-v02`

Qualified V1 baseline: `2825c4afe1a5b7a3ad40768c12cf480938a13d37`.

## Purpose

V2 connects the real-Mac-qualified Qwen3.8 + Codex Local Producer to the durable Supervisor while keeping production activation explicit and human review separate.

The default production Supervisor classes and Real Codex path remain unchanged. V2 adds separate `LocalWorktree*` classes that are bound to one explicit feature worktree and a separate Supervisor database.

## Safety model

- Local Qwen execution is disabled unless `enabled=True` is passed explicitly.
- The repository must be an exact non-symlink Git worktree root on a feature branch.
- `main`, `master`, and detached HEAD are rejected by the V1 workspace policy.
- Codex continues to use isolated `CODEX_HOME`, `workspace-write`, and `network_access=false`.
- The runner inherits only a bounded environment and does not forward API keys or credential variables.
- The Supervisor execution ledger records start and completion.
- A timeout or ambiguous external runner failure raises `LocalProducerExecutionUncertain`; the existing persisted stage runner then creates a mutation fence that requires reconciliation.
- Cancellation remains unsupported until exact-process cancellation is independently qualified.
- Local Qwen has no commit, push, merge, deploy, service-control, sudo, process-termination, or credential authority.
- Review remains a separate durable stage and Local Qwen cannot approve its own work.

## V2 components

`LocalWorktreeSupervisorRepository`
: Dedicated Supervisor repository bound to one validated feature worktree. Its DB defaults to `/Users/jerson/AI/runtime/supervisor-local-qwen/supervisor.db`.

`LocalWorktreeCodexTaskSpec`
: Dynamic worktree version of the existing durable Codex task contract.

`LocalQwenCodexRunner`
: Explicitly enabled `CodexTaskRunner` that checks the V1 bridge identity and invokes the qualified V1 Codex launcher.

`LocalWorktreeValidationRunner`
: Runs pytest against the feature worktree using the qualified control-plane Python.

`LocalWorktreeSecurityRunner`
: Applies the existing candidate and isolation security checks to the feature worktree.

`LocalWorktreeDurableReviewRunner`
: Consumes a separate durable review result scoped to the feature worktree.

`LocalWorktreeGitGateRunner`
: Read-only final gate that requires validation, review, and security PASS and still performs no Git mutation.

`LocalWorktreeWorkflowSupervisor`
: Uses feature-worktree review task contracts while retaining the existing durable workflow and lease behavior.

## Explicit construction

Creating these objects does not start a daemon or deploy anything.

```python
from pathlib import Path
from local_ai_control.services.supervisor import (
    LocalWorktreeSupervisorRepository,
    LocalWorktreeWorkflowSupervisor,
    create_local_qwen_job,
    local_qwen_runners,
)

root = Path("/absolute/path/to/feature-worktree")
repo = LocalWorktreeSupervisorRepository(root)
repo.migrate()
job, unit = create_local_qwen_job(
    repo,
    title="requested change",
    owner_id="owner",
    task_prompt="Inspect the code, implement the requested change, run tests, and report the result.",
)

# Still disabled. Producer/Revision will BLOCK without spawning Codex.
supervisor = LocalWorktreeWorkflowSupervisor(repo, local_qwen_runners(root))

# Explicit test/qualification opt-in only.
enabled = LocalWorktreeWorkflowSupervisor(repo, local_qwen_runners(root, enabled=True))
```

Production daemon wiring is intentionally absent in V2 until tests and independent review pass.

## Acceptance target

V2 must demonstrate:

1. disabled mode never spawns Codex;
2. feature-worktree fake launcher path passes;
3. main/symlink/path-scope violations fail closed;
4. V1 bridge identity mismatch blocks execution;
5. durable successful execution reaches `COMPLETED_CONFIRMED`;
6. timeout/uncertain execution creates an active mutation fence;
7. full control-plane tests remain green;
8. no production Supervisor restart, main merge, download resume, commit, push, merge, or deployment occurs during qualification.
