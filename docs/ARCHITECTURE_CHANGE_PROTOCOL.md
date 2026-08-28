# Architecture Change Protocol

Status: OWNER-APPROVED governance baseline

## Purpose

Material architecture decisions must be durable and discoverable. Chat memory alone is never sufficient.

## Required flow

`ARCHITECTURE_CHANGE_PROPOSED -> OWNER_APPROVED -> ADR_SYNCED -> IMPLEMENTATION_QUEUED -> IMPLEMENTED -> QUALIFIED -> INDEPENDENT_REVIEWED -> MERGE_GATE -> DEPLOY_GATE`

A short explicit approval such as `通过`, `可以`, or another unambiguous instruction is sufficient for the Owner-approval step.

## Where to record the decision

Use the smallest durable location that avoids branch sprawl:

1. Prefer the active implementation branch when it is safe to mutate.
2. If that branch is frozen for review/qualification, use the single long-lived `docs/architecture-ledger` branch.
3. Do not create a new docs branch for every decision.
4. Record the decision as an ADR under `docs/architecture/` and add/update the index.
5. Update `docs/CURRENT_STATUS.md` when the change affects current sequencing or project state.
6. Link the active implementation Issue/branch/qualification evidence.

## What requires an ADR

Use this flow for changes affecting one or more of:

- provider/model routing or priority;
- capability/authority boundaries;
- authentication/authorization/privacy/egress;
- durable task/job state machines;
- canonical storage/state ownership;
- external publishing or irreversible action gates;
- model/training architecture;
- shared workflow interfaces;
- repository governance/source-of-truth policy.

Ordinary bug fixes, contract-preserving refactors, test corrections, comments and spelling do not need a new ADR.

## Minimum ADR content

- status;
- context/problem;
- decision;
- scope;
- security/review boundaries;
- tracking Issue/branch;
- qualification/production state where relevant;
- supersedes/superseded-by relationship when relevant.

## Implementation behavior

Implementation agents must not silently introduce a new material architecture while executing an approved task. If implementation reveals a material design conflict, stop that architectural part and report the requested change for Owner decision.

## Qualification and status sync

After a material implementation phase, persist:

- START_HEAD / FINAL_HEAD;
- test/qualification evidence;
- review state;
- merge state;
- deployment/production state;
- known limitations/blockers.

Repeated failures that materially affect provider routing or architecture should be recorded as qualification evidence. A transient shell mistake does not require an ADR.

## No implicit activation

ADR acceptance, docs commits, feature commits, qualification PASS and independent review are distinct states. None automatically authorizes main merge, deployment, service restart, paid cloud use, external publishing or destructive cleanup.
