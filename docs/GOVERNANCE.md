# Repository Governance

Status: OWNER-APPROVED governance baseline

This document defines how project state is made durable and discoverable without creating branch sprawl or letting old Issue bodies become accidental sources of truth.

## 1. Canonical records

The repository uses different records for different purposes:

| Record | Purpose |
|---|---|
| `README.md` | Human/agent entrypoint and reading order |
| `docs/CURRENT_STATUS.md` | Cross-branch current-state snapshot |
| `docs/architecture/INDEX.md` | Architecture decision index |
| `docs/architecture/ADR-*.md` | Durable architecture/policy decisions |
| GitHub Issue | Execution tracking, acceptance checklist, blockers |
| feature branch | Implementation history |
| `docs/qualification/` or focused qualification docs | Evidence |
| annotated tag | Major qualified/released milestone only |

Chat memory is never the only durable source for an approved architecture or material provider/workflow decision.

## 2. Architecture decision flow

A material architecture decision follows:

`PROPOSED -> OWNER_APPROVED -> ADR_SYNCED -> IMPLEMENTATION_QUEUED -> IMPLEMENTED -> QUALIFIED -> INDEPENDENT_REVIEWED -> MERGED -> DEPLOYED`

Not every state must occur immediately. The record must explicitly distinguish them.

Material changes include provider routing, capability boundaries, privacy/egress, host permissions, state machines, long-term storage/canonical-state rules, external publishing gates, model/training architecture, review/quorum policy, and shared workflow interfaces.

Ordinary bug fixes, tests, comments, internal refactors preserving contracts, and spelling changes do not need new ADRs.

## 3. Branch policy

Do not create a separate docs branch for every architecture change.

Preferred order:

1. If a safe active implementation branch exists, record the ADR on that branch.
2. If the implementation branch is frozen for review/qualification, record architecture/status changes on the single long-lived `docs/architecture-ledger` branch.
3. A dedicated temporary docs branch is exceptional and should have a clear reason.

Branch lifecycle labels used in `CURRENT_STATUS.md`:

- `ACTIVE` — current implementation work.
- `FROZEN_REVIEW` — implementation complete enough that the baseline must not move during independent review.
- `REFERENCE` — preserved for evidence/history; no new work expected.
- `MERGED` — integrated into main.
- `RETIRED` — safe to delete after its durable records/evidence are preserved.

Never delete a branch solely to make the branch list cleaner. First ensure its unique decisions, qualification evidence, and relevant commits are represented by merged code, ADRs, Issues, or qualification records.

## 4. Issue policy

Issues track execution and acceptance. They are not automatically canonical architecture documents.

When an old Issue body becomes stale:

- add a visible status override or update the body;
- link the superseding ADR/current status/Issue;
- close it only when its execution purpose is complete, duplicated, or explicitly superseded.

Master issues #14 and #15 remain roadmap/product contracts, but `CURRENT_STATUS.md` determines what has actually been implemented, qualified, merged, or blocked today.

## 5. Current status policy

Update `docs/CURRENT_STATUS.md` after a material milestone, including:

- feature qualification;
- significant provider/routing change;
- branch freeze/unfreeze;
- model download/qualification change;
- production activation/deactivation;
- architecture blocker that changes sequencing;
- review/quorum architecture changes;
- stale Issue/branch cleanup that changes where future agents should look.

Do not store ephemeral PIDs as if they remain current. If a PID is useful evidence, label it historical and require live re-verification.

## 6. Qualification policy

Evidence must identify:

- exact branch and commit;
- scope;
- focused/full test counts where applicable;
- real runtime/API qualification where applicable;
- security/privacy result;
- known limitations;
- whether independent review occurred;
- whether merge/deployment occurred.

Repeated failures that materially affect model/provider routing should be recorded as qualification evidence. One transient shell mistake does not need architecture documentation.

Local-model qualification must also preserve the intended desktop workload. Production promotion may not rely on an artificially emptied machine when the platform is intended to coexist with normal work applications.

Hard rules for local-model qualification:

1. Every live run declares a workload class: `LAB`, `REPRESENTATIVE_WORKLOAD`, or `STRESS_COEXISTENCE`.
2. `REPRESENTATIVE_WORKLOAD` is the mandatory promotion gate for a normal desktop production default.
3. Qualification automation MUST NOT close, pause, suspend, or kill user applications to create headroom. This includes browsers, Unity, IDEs, ChatGPT/Codex, terminals, communication tools, media apps, and other normal desktop processes.
4. A harness may terminate only exact-owned model/qualification processes whose identity is verified.
5. A reduced-workload result is `LAB` evidence. LAB success cannot erase or supersede a representative-workload resource failure.
6. Representative failures should become routing/admission constraints, such as using a smaller qualified local model, queueing until resources recover, using an approved provider fallback, or declining the heavy route with an explicit reason.
7. Evidence must record a workload manifest sufficient to explain material host load and whether any application was deliberately closed.
8. Functional success, resource qualification, workload class, and production promotion status must remain separate claims.

The canonical detailed method is `docs/qualification/WORKLOAD_QUALIFICATION_POLICY.md`, with architecture rationale in `docs/architecture/ADR-0005-workload-aware-local-model-admission.md`.

## 7. Download-state governance

Model download state is particularly easy to misread. Use these rules:

1. A valid completion marker plus snapshot validation is the authority for `COMPLETE`.
2. `payload_bytes` is usable completed payload physically present.
3. `.incomplete` cache is resumable work, not completed payload.
4. Duplicate/abandoned `.incomplete` fragments can exceed expected model size and must not be presented as a meaningful percent-complete number.
5. Canonical progress reporting should therefore show payload percentage and partial-cache size separately.
6. Stored manager PID/identity is historical unless live identity verification passes.
7. Queue config, runtime state, and local directories must agree on model ID/repo/revision before resume.
8. Network pause does not authorize cleanup of resumable caches.

See `docs/DOWNLOAD_STATUS.md` for the latest audit.

## 8. Autonomous review governance

The canonical architecture is
`docs/architecture/ADR-0006-autonomous-review-mesh.md`. The normative schemas,
digests, state machines and fail-closed outcomes are in
`docs/architecture/REVIEW_MESH_PROTOCOL_V1.md`; detailed reviewer qualification
and bootstrap rules live in
`docs/qualification/REVIEWER_QUALIFICATION_POLICY.md`.

Hard rules:

1. Mesh review extends the existing immutable Supervisor `TaskObjective`,
   `ReviewTaskSpec`, `CandidateIdentity`, safe-file manifest, review round and
   persisted-finding contracts. Mutable PR/Issue prose cannot replace the task
   objective or review scope.
2. A canonical request/result binds the task/objective, exact base and candidate
   SHAs, candidate/review generations, scope/material, complete contributor
   history, deterministic gates, active risk/privacy/quorum policy decisions,
   registry snapshots and a unique invocation nonce/receipt.
3. Candidate and review generations are monotonic and never reused. A base
   change, H1 -> H2 -> H1, or any bound gate/scope/policy/registry change keeps
   earlier votes stale even when a SHA reappears.
4. Producer/Fixer/Reviewer identity is established by trusted orchestrator
   ingestion of an authenticated adapter/provider execution, not a claimed
   string. Unexpected fallback uses only the actual model's own lineage and
   qualification.
5. Independence uses a versioned canonical foundation-lineage equivalence
   registry and the complete Producer/Fixer contributor history. Same-foundation
   aliases/hosts count once; unknown or ambiguous lineage is `NON_INDEPENDENT`.
6. A Producer/Fixer cannot satisfy its own required independent review. A
   contributor family remains non-independent after a different family repairs
   the candidate.
7. A reviewer must be qualified for the exact actual identity, requested risk,
   protocol, benchmark, harness and registry snapshots before a vote counts.
8. P0 and P1 each require at least two qualified strong reviewers from distinct
   independent foundation equivalence classes. Subsystem policy may increase,
   never reduce, those floors.
9. Risk, privacy/egress, security gates, reviewer class and quorum are derived by
   trusted versioned policy from immutable task/change/data manifests. Model or
   caller input may escalate but never downgrade. Ambiguity fails closed to the
   stricter policy.
10. Changes to risk, privacy, lineage, qualification or quorum policy are
    evaluated under the previous approved policy until independent review and
    explicit Owner authorization activate the new revision.
11. A model PASS is evidence, not ground truth, state-transition authority or
    merge/deploy authorization.
12. BLOCKING/HIGH findings receive stable IDs and survive candidate/result
    staleness until independently `VERIFIED_CLOSED` or `DISMISSED`. Delivery to a
    Fixer and later PASS votes do not close them.
13. Material findings are strengthened with deterministic tests, reproducers,
    invariant checks or exact call-path/state-transition evidence where feasible.
14. Public regression fixtures are necessary but insufficient for Strong P0/P1
    qualification; separately sealed held-out fixtures and custody evidence are
    mandatory.
15. Initial reviewer-registry trust is created only through the one-time
    `BOOTSTRAP_V1` ceremony. After `BOOTSTRAP_COMPLETE`, normal Mesh rules are
    mandatory; reopening is a P0 Owner-authorized governance event.
16. GitHub is a queue/notification bus, not canonical quorum state. Canonical
    records live in the Owner-private append-only ledger; comments reference
    immutable digests and cannot be edited into or out of quorum.
17. Automatic repair is finite and oscillation/no-progress bounded. Every repair
    creates a new candidate generation and reruns required gates/review.
18. If qualified independent capacity alone is absent, use
    `WAITING_FOR_INDEPENDENT_REVIEW`; never lower standards or privacy. Identity,
    ledger or policy ambiguity uses a blocked-reconciliation state.
19. Repository text, PR/Issue content, generated bundles and model findings are
    untrusted. They cannot change policy, disclose secrets, expand permissions or
    mark a candidate PASS.
20. Review infrastructure does not authorize merge, deploy, service restart,
    production registry promotion, external publication, destructive cleanup,
    privilege expansion or unapproved paid cloud use.

Initial risk policy:

- `P0`: runtime mutation, security, credentials, deployment, automatic
  execution, privilege and privacy/egress gates. Requires deterministic gates,
  at least two qualified `STRONG_P0` independent foundation-family votes, no
  unresolved BLOCKING/HIGH finding, and explicit Owner authorization.
- `P1`: architecture, routing, durable state, review governance, sensitive-data
  handling and shared workflow contracts. Requires deterministic gates, at
  least two qualified `STRONG_P1` independent foundation-family votes, no
  unresolved BLOCKING/HIGH finding, and explicit Owner authorization.
- `P2`: ordinary bounded feature work. Requires deterministic gates and at least one qualified independent reviewer unless subsystem policy is stricter.
- `P3`: low-risk mechanical/docs work. May use a lighter reviewed process where allowed, while preserving exact SHA and deterministic evidence binding.

## 9. Merge and production gates

A docs/feature commit does not imply main merge. A merged commit does not imply production activation.

Where independent review is required by subsystem policy, keep the branch frozen until review finishes. Merge, deploy, service restart, paid cloud use, external publish, and destructive cleanup remain separate explicit gates.

A review quorum only means the review requirement is satisfied for the exact candidate/evidence it covers. It never consumes the Owner merge/deploy authorization gate.

## 10. New-conversation bootstrap

A new human/agent session should read only the smallest canonical set first:

1. `README.md`
2. `docs/CURRENT_STATUS.md`
3. `docs/architecture/INDEX.md`
4. relevant ADR
5. active implementation Issue/branch
6. relevant qualification evidence

Only then inspect historical branches/issues if a specific ambiguity remains.
