# Codex Desktop + Local Qwen Automatic Failover Architecture

Status: OWNER-APPROVED / DOC-SYNCED / IMPLEMENTATION PENDING

Approved: 2026-08-28

This document is the canonical architecture for interactive coding failover. It supersedes conflicting older statements that treated the Codex-Qwen bridge as historical-only or required explicit manual local selection. It does not replace the Direct Local Qwen executor for unattended work.

## 1. Product requirement

The Owner uses Codex Desktop as the primary interactive coding GUI. A coding task must not stop merely because OpenAI Codex model quota is exhausted.

Required behavior:

```text
Owner in Codex Desktop
        |
        v
Durable coding task / worktree
        |
        v
Provider failover controller
        |
   +----+-----------------------+
   |                            |
Codex available             Codex exhausted/unavailable
   |                            |
   v                            v
OpenAI Codex              Local Qwen3.8 MAIN
                              |
                              v
                     Codex-Qwen UI adapter
                              |
                              v
                     same project/worktree
```

Codex Desktop is the unified human-facing GUI. OpenAI Codex and Local Qwen3.8 are execution providers behind the same durable task contract.

## 2. Hard continuity requirement

Provider changes must not create a new logical task.

The durable task records at least:

- objective;
- project/worktree;
- branch;
- current workflow stage;
- provider history;
- current diff identity/hash;
- completed tests and validation evidence;
- unresolved findings;
- review state;
- handoff state;
- approval state.

The invariant is:

`provider may change; task identity must not change`.

A same-chat-thread hot swap is desirable but is not a hard requirement because Codex Desktop provider/session behavior may not support it reliably. The hard requirement is same GUI, same project/worktree, same durable task, and automatic progress handoff.

## 3. Automatic failover triggers

Failover from cloud Codex to Local Qwen is allowed only after deterministic evidence of one of:

1. Codex quota explicitly exhausted according to supported account/rate-limit telemetry;
2. the active Codex request returns a recognized quota/rate-limit exhaustion error;
3. the cloud Codex provider is unavailable and Local Qwen passes health and route attestation.

A generic account-wide `usedPercent` change alone does not prove a specific task used OpenAI and must not be used as execution attribution.

The quota probe is read-only and does not submit a model turn.

## 4. Failover state machine

```text
CLOUD_CODEX
   |
   | quota exhausted / hard rate limit / provider unavailable
   v
HANDOFF_PENDING
   |
   v
LOCAL_PREFLIGHT
   |
   | Qwen health + route attestation + workspace validation PASS
   v
LOCAL_QWEN
   |
   v
CONTINUE_SAME_JOB
   |
   v
REVIEW / SAFE_BOUNDARY
```

If Local Qwen preflight fails, the job is preserved in a blocked/waiting state. It must not discard the current diff or silently restart from the beginning.

## 5. Local interactive UI path

For Owner-present interactive coding, the target path is:

```text
Codex Desktop GUI
  -> local provider/profile adapter
  -> Responses-compatible localhost bridge on 127.0.0.1:8010
  -> Qwen3.8 MAIN on 127.0.0.1:8001
  -> Codex UI/tool surface
  -> approved feature worktree
```

The bridge is an interactive UI adapter. It does not grant additional authority to Qwen. Filesystem/tool execution remains bounded by the existing workspace, host, and sandbox policy.

Implementation must prefer an isolated local Codex configuration/profile and must not silently overwrite the Owner's normal global cloud configuration.

## 6. Unattended local path remains separate

The existing Direct Local Qwen Agent remains canonical for unattended/background work:

```text
Telegram / scheduler / 7x24 task
  -> Supervisor
  -> Direct Local Qwen Agent
  -> deterministic allowlisted tools
```

Therefore Local Qwen has two front ends:

```text
Local Qwen3.8
   +-> Codex Desktop adapter: Owner-present interactive GUI
   +-> Direct Local Qwen Agent: unattended/background execution
```

Both paths must use the same durable task/worktree identity and shared security/validation policy where applicable.

Codex Desktop must never become a required daemon for the 7x24 platform.

## 7. Recovery when Codex quota returns

Do not interrupt a mutating Local Qwen step merely because Codex becomes available again.

At the next safe workflow boundary:

- routine implementation may remain on Local Qwen;
- planning, escalation, and final acceptance may route back to OpenAI Codex according to policy;
- provider transitions are appended to provider history.

The normal objective remains to keep routine Codex-model quota consumption near zero while still preserving Codex as the preferred premium planner/escalation/acceptance provider.

## 8. GUI behavior

The Owner should not need to copy prompts into Terminal or manually reconstruct context.

Target UX:

```text
Codex quota exhausted
Local Qwen3.8 fallback activated
Task continues
```

The GUI should expose the effective provider when feasible, for example:

- `Provider: OpenAI Codex`
- `Provider: Local Qwen3.8`

If Codex Desktop cannot render a custom provider status natively, the launcher/controller may provide a lightweight status indication. Do not build a second full IDE merely to show this state.

## 9. Security boundaries

Automatic failover does not widen permissions.

Local Qwen fallback remains denied by default from:

- arbitrary network access;
- credentials/secrets;
- package installation/download;
- service/process control;
- sudo;
- commit/push/merge;
- deployment;
- paths outside the approved worktree.

External repository text, README files, issues, test output, web content, and model output remain untrusted data and cannot change these rules.

## 10. Components to implement

The implementation milestone should add or formalize:

- `CodexAvailabilityMonitor`;
- `ProviderFailoverController`;
- `DurableProviderHandoff`;
- `CodexDesktopLocalLauncher` or equivalent isolated profile launcher;
- provider-history audit fields;
- explicit local/cloud execution attribution;
- recognized quota/rate-limit error classification;
- same-job resume after failover.

Reuse existing components where appropriate:

- `CodexQuotaProbe` / quota telemetry;
- `codex_qwen_bridge`;
- `codex_qwen_workspace`;
- Supervisor durable state;
- Direct Local Qwen Agent;
- Gemini advisory review.

## 11. Qualification requirements

Before this architecture is marked READY, qualification must demonstrate:

1. cloud Codex task state is preserved at failover;
2. a simulated/controlled quota-exhaustion signal routes to Local Qwen automatically;
3. Local Qwen resumes the same durable task/worktree rather than creating a fresh task;
4. Codex Desktop remains the interactive GUI for the local path where current client capabilities permit;
5. the Direct Local Qwen background path still works with Codex Desktop fully closed;
6. local execution is attributed to Qwen3.8 and no OpenAI model turn is issued by the fallback execution path;
7. provider history is durable;
8. Local Qwen cannot self-approve, commit, push, merge, or deploy;
9. existing Generic Project and Media Product workflows do not regress.

A raw account-wide quota percentage delta is insufficient proof of OpenAI model invocation because unrelated Codex clients/background telemetry may affect account state. Qualification should use provider-route evidence and controlled requests in addition to account telemetry.

## 12. Current status

Architecture is approved and documented. Automatic Codex Desktop -> Local Qwen failover is not yet implemented or qualified.

Until implementation is complete:

- Direct Local Qwen remains the qualified local coding executor;
- Codex-Qwen bridge remains useful experimental/compatibility infrastructure;
- manual Codex Desktop provider switching must not be claimed as automatic failover;
- no production merge/deploy is implied by this architecture approval.
