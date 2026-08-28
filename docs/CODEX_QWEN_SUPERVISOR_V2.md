# Local Producer V2: Supervisor integration

Status: HISTORICAL V2 BASELINE / SUPERSEDED WHERE CONFLICTING BY AUTO-FAILOVER ARCHITECTURE

Original branch: `feat/codex-qwen-supervisor-v02`

Qualified V1 baseline referenced by the original design: `2825c4afe1a5b7a3ad40768c12cf480938a13d37`.

## Purpose

V2 introduced durable Supervisor integration for the Local Qwen + Codex producer while keeping worktree scope, review, validation, and Git gates separate.

On 2026-08-28 the Owner approved a broader requirement: when an interactive Codex task encounters verified quota exhaustion/rate-limit/provider unavailability, Local Qwen must automatically take over the same durable task while Codex Desktop remains the primary Owner-facing GUI. See `CODEX_AUTO_FAILOVER_ARCHITECTURE.md`.

Therefore the old V2 rule that Local Qwen is only activated by an explicit `enabled=True` selection is no longer the final target for interactive provider failover. Explicit enablement remains appropriate for low-level tests and for fail-closed component construction.

## Durable Supervisor role

Supervisor remains the continuity authority across provider changes.

A durable coding job should preserve:

- job/task identity;
- objective;
- validated feature worktree and branch;
- current stage;
- diff/candidate identity;
- validation/test evidence;
- review findings;
- approval state;
- provider history;
- handoff/resume state.

A provider switch from OpenAI Codex to Local Qwen must not create a fresh logical job.

## Target provider-aware flow

```text
Durable Supervisor job
        |
        v
ProviderFailoverController
        |
   +----+------------------+
   |                       |
CLOUD_CODEX           LOCAL_QWEN
   |                       |
   +----------+------------+
              |
              v
      same worktree/state
              |
              v
 validation -> review -> security -> Git Gate
```

The controller may enter the local route automatically only after deterministic supported evidence such as explicit quota exhaustion, a recognized active-request quota/rate-limit failure, or cloud-provider unavailability with healthy Local Qwen preflight.

## Safety model

The following constraints remain:

- repository must be an exact non-symlink Git worktree root on a feature branch;
- `main`, `master`, and detached HEAD are denied for mutating task execution;
- Local Qwen route must be health-checked and route-attested;
- bounded environment; do not forward ambient credential variables;
- execution start/completion and provider transitions are journaled;
- uncertain mutating failures create a reconciliation/mutation fence rather than blind replay;
- Local Qwen cannot approve its own work;
- commit, push, merge, deployment, sudo, credentials, and service/process control remain outside model authority.

## Existing components to reuse

The original V2 concepts remain useful:

`LocalWorktreeSupervisorRepository`
: Durable repository bound to one validated feature worktree.

`LocalWorktreeCodexTaskSpec`
: Worktree-aware durable coding task contract.

`LocalWorktreeValidationRunner`
: Focused/test validation bound to the approved worktree.

`LocalWorktreeSecurityRunner`
: Candidate and isolation security checks.

`LocalWorktreeDurableReviewRunner`
: Separate durable review result; producer cannot self-approve.

`LocalWorktreeGitGateRunner`
: Final read-only gate requiring validation, review, and security evidence before any separately authorized Git mutation.

`LocalWorktreeWorkflowSupervisor`
: Durable stage machine and lease behavior.

The older `LocalQwenCodexRunner` may be retained as compatibility code or adapted for the interactive Desktop local-provider route, but the final routing contract is provider-aware rather than a manually selected Local-Qwen-only Supervisor.

## New components required

Implementation should add or formalize:

- `CodexAvailabilityMonitor`;
- `ProviderFailoverController`;
- `DurableProviderHandoff`;
- provider-history records;
- recognized quota/rate-limit error classification;
- same-job resume after failover;
- Codex Desktop local-provider launcher/adapter for Owner-present work;
- explicit execution-provider attribution.

The existing read-only Codex quota probe may be reused as one signal. Account-wide `usedPercent` changes alone are not enough to attribute a model turn to a task.

## Interactive vs unattended routes

Owner-present interactive coding:

```text
Codex Desktop
  -> Supervisor/durable task
  -> Cloud Codex or Local Qwen provider
  -> same worktree
```

Unattended/background coding:

```text
Telegram / scheduler
  -> Supervisor
  -> Direct Local Qwen Agent
  -> same durable worktree/security contracts
```

Codex Desktop must never become a required daemon for unattended platform work.

## Recovery behavior

When Codex quota later becomes available, do not interrupt an active mutating Local Qwen step. At a safe boundary the router may choose:

- keep routine implementation on Local Qwen;
- return PLANNING / ESCALATION / ACCEPTANCE work to OpenAI Codex;
- append the transition to provider history.

## Acceptance target for the new milestone

The next qualification must demonstrate:

1. cloud task state is preserved before failover;
2. a controlled exhaustion/rate-limit signal causes automatic local routing;
3. Local Qwen resumes the same durable job/worktree;
4. Codex Desktop remains the interactive GUI where client capabilities permit;
5. Direct Local Qwen still works with Codex Desktop closed;
6. local fallback execution is attributable to Qwen3.8 and does not issue an OpenAI model turn;
7. provider history and handoff state survive restart;
8. uncertain mutation still creates a reconciliation fence;
9. Local Qwen cannot self-approve or mutate Git history;
10. existing Generic Project and Media Product workflows remain green.

Architecture approval does not imply merge, deployment, production daemon activation, or automatic service restart.
