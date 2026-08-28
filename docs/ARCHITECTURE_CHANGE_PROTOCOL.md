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

## Implementation handoff

Every material implementation prompt should identify the approved branch/ref and instruct the agent to read the relevant architecture documents before coding. The implementation report must identify its START_HEAD and FINAL_HEAD and distinguish code-complete, qualified, deployed, and published states.

## No implicit production activation

Documentation approval and feature-branch commits do not imply merge, canonical deployment, public activation, model qualification, external publishing, or destructive cleanup. Those remain separate gated actions according to their subsystem policies.
