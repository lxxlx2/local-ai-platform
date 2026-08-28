# Architecture Change Protocol

Status: OWNER-APPROVED governance baseline.

## Purpose

Architecture decisions must be durable and visible to future ChatGPT conversations, Codex, Local Qwen, Gemini reviewers, and other engineering agents. A material architecture decision made only in chat is not considered implemented policy.

## Required flow

```text
ARCHITECTURE_CHANGE_PROPOSED
  -> design is described to Owner before repository mutation
  -> OWNER_APPROVED
  -> DOC_SYNC
  -> commit SHA / branch HEAD recorded
  -> IMPLEMENTATION_QUEUED
  -> implementation agent reads the approved architecture HEAD
  -> focused implementation/tests
  -> QUALIFICATION / REVIEW
  -> STATUS_SYNC
```

The normal approval may be a short explicit Owner response such as `通过`, `可以`, `按这个来`, or another unambiguous approval.

## Chat-originated architecture durability

Any new operating mode, provider-routing rule, workflow architecture, review policy, state-machine change, capability boundary, or other material architecture idea that is approved during a ChatGPT/Codex/Local-Qwen/Gemini conversation must be synchronized to Git promptly.

The synchronization does not require merging to `main`.

A dedicated docs/design branch, architecture issue, or feature branch is acceptable as long as the decision is durable, discoverable, and carries enough status to prevent later agents from treating it as deployed production behavior.

The minimum durable record should include, where applicable:

- decision / intended behavior;
- scope and affected workflows;
- provider/model priority or capability changes;
- security/review boundaries;
- current implementation status;
- qualification status;
- branch/ref or issue that tracks implementation;
- explicit statement when merge/deployment/production activation has not happened.

Do not rely on chat memory as the only source of truth for an approved architecture decision.

When a conversation produces a material architecture update, the preferred order is:

```text
OWNER_APPROVES_IN_CHAT
  -> WRITE_DURABLE_GIT_RECORD
  -> RECORD_BRANCH_OR_ISSUE_REFERENCE
  -> CONTINUE_IMPLEMENTATION
```

If the implementation branch must remain frozen for review or qualification, create a separate docs/design branch instead of mutating the frozen review baseline.

## What requires this flow

Use this protocol when a proposed change materially affects one or more of:

- user-facing workflow or interaction model;
- capability boundaries or model/provider routing;
- authentication, authorization, privacy, egress or host permissions;
- durable job/task state machines;
- data ownership, storage roots, retention or deletion policy;
- external publishing or irreversible side effects;
- model/persona/training architecture;
- canonical repository/product artifact layout;
- application-level interfaces that multiple workflows will depend on.

Ordinary bug fixes, internal refactors that preserve contracts, test corrections, comments, spelling, and implementation-detail changes do not require a separate architecture approval round.

## Agent behavior

Codex, Local Qwen and other implementation agents must not silently introduce a material architecture change while executing an approved task.

If implementation discovers that an approved architecture must materially change, the agent should stop that architectural part and report:

```text
ARCHITECTURE_CHANGE_REQUEST
proposed_change: ...
reason: ...
affected_contracts: ...
implementation_blocked: true/false
```

The Owner-facing planning layer then follows the normal proposal -> approval -> doc-sync flow.

## Documentation sync

DOC_SYNC updates the smallest relevant set of canonical documents. Depending on scope this can include:

- `docs/ARCHITECTURE.md`
- subsystem architecture documents
- `docs/BOT_UX.md`
- `docs/CURRENT_STATUS.md`
- active master plan / roadmap issues when their execution order or end-state meaning changes
- product repository README/specification when an external artifact contract changes

Generated files such as `CAPABILITY_MATRIX.md` must be updated through their generator/evidence source rather than hand-edited.

Operational failures that materially affect architecture or provider-routing decisions should also be recorded in the relevant issue or qualification evidence. A transient command failure does not require architecture documentation by itself, but repeated or architecture-relevant failure patterns do.

## Implementation handoff

Every material implementation prompt should identify the approved branch/ref and instruct the agent to read the relevant architecture documents before coding. The implementation report must identify its START_HEAD and FINAL_HEAD and distinguish code-complete, qualified, deployed, and published states.

## No implicit production activation

Documentation approval and feature-branch commits do not imply merge, canonical deployment, public activation, model qualification, external publishing, or destructive cleanup. Those remain separate gated actions according to their subsystem policies.
