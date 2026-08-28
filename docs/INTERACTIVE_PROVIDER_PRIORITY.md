# Interactive Provider Priority and Review Independence

Status: OWNER-APPROVED / DOC-SYNCED / IMPLEMENTATION PENDING

Approved: 2026-08-28

## 1. Scope

This document defines provider priority for Owner-present interactive engineering work initiated from Codex Desktop.

It does not replace the platform-wide local-first policy in `LOCAL_FIRST_PRODUCT_AND_MODEL_PLAN.md`.

For unattended/routine production, Local Qwen remains the default worker according to the local-first master plan.

For Owner-present interactive engineering sessions, Codex Desktop remains the unified human-facing GUI and the provider priority is:

```text
Codex Desktop GUI
    |
    v
Durable task / approved worktree
    |
    v
P1 OpenAI Codex
    |
    | unavailable / quota exhausted / bounded escalation
    v
P2 Local Qwen3.8 MAIN
    |
    | unavailable / bounded repeated failure / supplemental capability needed
    v
P3 Gemini API supplementary provider
    |
    v
Review by a different provider + host/Owner gate
```

The priority order is therefore:

1. `P1 = OpenAI Codex`
2. `P2 = Local Qwen3.8 MAIN`
3. `P3 = Gemini API supplementary provider`

This order applies only to the Owner-present Codex Desktop interactive path.

## 2. Unified GUI requirement

Codex Desktop is the preferred single human-facing interface.

The Owner should not have to manually reconstruct task context when the provider changes.

The hard continuity requirement is:

- same durable task;
- same project/worktree;
- same branch;
- same objective;
- preserved candidate/diff state;
- preserved validation/review evidence;
- append-only provider history;
- automatic progress handoff.

Exact same-chat-thread hot swap remains desirable but is not a hard requirement because current Codex Desktop evidence does not prove atomic provider replacement inside an already-running chat thread.

Provider/session boundaries may change while the durable task identity must remain stable.

## 3. P1 OpenAI Codex

OpenAI Codex is the first-priority reasoning/engineering provider for Owner-present interactive sessions when:

- Codex model quota is available;
- the Owner intentionally chose the interactive premium path;
- the task is eligible under privacy/security policy.

Codex may perform planning, implementation, diagnosis, revision and acceptance according to existing host policy.

Codex model quota usage must remain distinct from use of the Codex CLI executable configured with a local provider.

## 4. P2 Local Qwen3.8 MAIN

Local Qwen3.8 is the second-priority provider for the interactive path.

It is eligible when:

- Codex quota is deterministically exhausted;
- Codex returns an attributable supported rate-limit/quota error;
- Codex provider is unavailable;
- the Owner explicitly selects local execution;
- policy routes a bounded continuation to local execution.

The existing qualified Codex Desktop -> Local Qwen failover architecture remains authoritative for this transition.

Local Qwen keeps its existing restricted machine authority and cannot self-approve, commit, push, merge, deploy, access secrets, widen network access or alter host policy.

## 5. P3 Gemini API supplementary provider

Gemini is the third-priority supplementary provider for Owner-present interactive sessions.

This extends its existing independent-review role. It does not silently turn Gemini into a privileged machine controller.

Gemini may be considered when:

- Codex is unavailable/exhausted and Local Qwen preflight fails;
- Local Qwen repeatedly fails a bounded task and the task still benefits from another reasoning provider;
- an independent cloud second opinion is useful before the next provider transition;
- a multimodal/long-context capability is useful and privacy policy permits cloud egress.

Gemini remains subject to the existing cloud egress/privacy gate:

- `PUBLIC`: allowed;
- `RESTRICTED`: allowed only after minimization/sanitization;
- `PRIVATE`: denied.

The platform must never silently enable paid Gemini billing.

### 5.1 Gemini execution boundary

The preferred implementation does not give Gemini direct shell/Git/deploy authority.

For coding/repair work, Gemini should produce a bounded structured proposal such as:

- patch/diff candidate;
- file-scoped edit plan;
- diagnosis with exact recommended changes;
- structured continuation artifact.

A host-controlled local executor may apply an approved structured patch inside the approved worktree and run deterministic tests.

This allows Gemini to contribute real engineering work without granting it ambient filesystem, shell, Git, service-control or deployment authority.

### 5.2 Codex Desktop integration

Gemini should be exposed through the Codex Desktop interaction surface where feasible, preferably through MCP/host tools backed by the existing Gemini API gateway and privacy gate.

Candidate tools include future bounded operations such as:

- `gemini_ask`
- `gemini_review_code`
- `gemini_review_architecture`
- `gemini_propose_patch`
- `gemini_continue_task`

The tools must return structured artifacts and never grant Gemini authority beyond the host-side contract.

## 6. Independent review rule

A provider may perform a preliminary self-check, but it must not be the sole final reviewer/acceptor of its own produced candidate.

The required rule is:

```text
producer != final independent reviewer
```

Examples:

- Codex-produced candidate -> review by Qwen and/or Gemini before Owner/host acceptance;
- Qwen-produced candidate -> review by Codex and/or Gemini before Owner/host acceptance;
- Gemini-produced candidate -> review by Codex and/or Qwen before Owner/host acceptance.

Gemini therefore cannot be the sole final reviewer of a Gemini-produced patch or implementation.

Local Qwen cannot be the sole final reviewer of Qwen-produced work.

Codex cannot be the sole independent reviewer of Codex-produced work when an independent-review gate is required.

If no independent provider is currently available, the task remains `REVIEW_PENDING` or blocked at the appropriate safe boundary. It must not silently self-approve.

Irreversible external actions, merge, deployment and production activation remain Owner/host gated.

## 7. Provider routing state

The target Owner-present interactive provider sequence is:

```text
P1_CLOUD_CODEX
   |
   | deterministic Codex unavailability/quota trigger
   v
P2_LOCAL_QWEN_PREFLIGHT
   |
   +--> PASS -> P2_LOCAL_QWEN
   |
   +--> FAIL / bounded repeated failure
               |
               v
         P3_GEMINI_PREFLIGHT
               |
               +--> privacy/egress/API PASS -> P3_GEMINI_SUPPLEMENT
               |
               +--> FAIL -> WAITING / BLOCKED
```

Provider recovery occurs only at safe workflow boundaries.

A provider becoming available again must not interrupt an active mutating step.

## 8. Durable provider history

Every provider transition must preserve the same logical task and append auditable provider history.

At minimum record:

- job/task identity;
- from-provider;
- to-provider;
- reason/classification;
- workflow stage;
- objective binding;
- worktree/branch binding;
- candidate/diff identity where applicable;
- test/review evidence references;
- timestamp;
- safe-boundary state.

Gemini activity must be explicitly attributed as Gemini API activity and must never be confused with OpenAI Codex model usage or Local Qwen execution.

## 9. Current implementation status

Already implemented/qualified on `feat/codex-desktop-auto-failover-v01`:

- P1 OpenAI Codex -> P2 Local Qwen automatic same-job failover;
- durable provider history for the qualified Codex/Qwen path;
- Local Qwen preflight and route attestation;
- safe-boundary recovery;
- isolated Codex local-provider adapter;
- exact local execution cancellation;
- bounded execution tracing;
- Gemini advisory review through the Gemini API/privacy gateway.

Not yet implemented/qualified as of this document sync:

- P2 -> P3 automatic/supervised Gemini supplementary routing;
- Gemini structured patch/continuation provider;
- host-controlled Gemini patch application path;
- Codex Desktop MCP tools for Gemini task continuation/proposed patches;
- cross-provider independent-review assignment enforcing `producer != final independent reviewer` across all three providers;
- full three-provider qualification.

Therefore the three-provider policy is architecture-approved and doc-synced, implementation pending.

## 10. Qualification requirements for P3

Before P3 is marked READY, prove:

1. Codex unavailable -> Qwen preflight failure/repeated bounded failure -> eligible Gemini preflight;
2. PRIVATE material never reaches Gemini;
3. RESTRICTED material passes the existing egress gate;
4. Gemini receives only bounded/minimized task material;
5. Gemini cannot directly execute shell/Git/deploy/service actions;
6. Gemini structured patch/proposal is bound to the same durable task/worktree/objective;
7. host patch application cannot escape the approved worktree;
8. Gemini-produced changes are reviewed by Codex or Qwen before final acceptance;
9. Gemini cannot self-approve its own candidate;
10. no provider transition creates a new logical task;
11. provider history remains append-only/idempotent;
12. Codex Desktop remains the preferred Owner-facing GUI where current client capabilities permit;
13. no silent paid Gemini usage is enabled;
14. existing Codex -> Qwen failover qualification does not regress.

## 11. Relationship to local-first policy

This document defines provider priority only for the Owner-present interactive Codex Desktop engineering path.

The broader platform remains local-first:

```text
unattended/routine production -> Local Qwen / local specialists
Owner-present premium interactive engineering -> Codex > Local Qwen > Gemini supplementary
independent review -> provider different from the producer when required
```

This distinction preserves the existing goal of near-zero routine Codex-model quota usage while giving the Owner a single premium interactive engineering surface with deterministic fallbacks.
