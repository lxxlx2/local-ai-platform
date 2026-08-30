# ADR-0006: Autonomous Review Mesh

Status: ACCEPTED / PROTOCOL HARDENED / IMPLEMENTATION QUEUED

Date: 2026-08-30

Owner approval: architecture approved in project conversation before G0
implementation. The V1 trust-model hardening responds to the independent review
of PR #35 at `f219b087c7210297981c3005c380b7634e68523a`; the hardened candidate
requires fresh independent review.

Tracking: Issues #32, #33, #34. Related: #14, #15, #24, PR #31.

Normative protocol:
`REVIEW_MESH_PROTOCOL_V1.md`

Normative reviewer qualification policy:
`../qualification/REVIEWER_QUALIFICATION_POLICY.md`

## Context

The platform is intended to reduce marginal AI cost and routine Owner
involvement by allowing one task to progress through implementation,
deterministic verification, independent review, repair and re-review without
manual copying between AI conversations.

The existing review path has two demonstrated weaknesses. First, a provider may
be the only available Producer and Reviewer, making role-name separation an
illusory same-family self-review. Second, PR #31 candidate
`a94fd5886a12c744c0e7ccd48cf7ea31124968f2` received Gemini 3.6 PASS/zero
findings even though later semantic verification found a BLOCKING
planner-to-runtime workload/evidence TOCTOU. The candidate was kept out of the
Owner gate and repaired at `cab62d8526b56f20ac49c36b27accef0877d774e`.

The first PR #35 architecture review then exposed deeper protocol risks:
mutable task objectives, replayable result binding, forgeable identity and
qualification claims, ambiguous model lineage, reducible P1 quorum,
producer-controlled classification, circular qualification bootstrap, public
benchmark leakage, mutable GitHub evidence and material findings that could be
lost when a candidate changed.

Therefore a model PASS, provider brand, model size, schema-valid comment or
candidate SHA by itself is not sufficient proof of correctness or freshness.

## Decision

Adopt an Autonomous Review Mesh controlled by a deterministic trusted
orchestrator.

Canonical flow:

`TaskObjective/TaskSpec -> Producer -> candidate generation -> deterministic gates -> immutable review campaign -> qualified independent reviewers -> finding verification -> bounded Fixer -> new candidate generation -> repeat -> quorum -> Owner gate`

LLMs supply proposals, findings and repairs. The orchestrator derives policy,
constructs authenticated request/result envelopes, maintains append-only state
and decides whether evidence is current and sufficient.

The detailed schemas, digests, state outcomes and fail-closed rules in
`REVIEW_MESH_PROTOCOL_V1.md` are normative. An implementation that omits a V1
mandatory field or invariant is not ADR-0006 compliant.

## 1. Layer on the Workflow Supervisor

The Mesh reuses rather than replaces these existing Supervisor contracts:

- Owner-private immutable `TaskObjective`;
- `ReviewTaskSpec` and safe-file manifest;
- `CandidateIdentity`, trusted base, candidate diff and rename/copy provenance;
- review work units and monotonic review rounds;
- normalized `ReviewFinding` and `ReviewResult`;
- persisted review findings;
- producer/reviewer separation and active-candidate result reconciliation.

Mesh V1 adds authenticated execution identities, complete contributor history,
canonical lineage, qualification, policy decisions, monotonic candidate/review
generations, request/result binding, quorum and an append-only ledger above
those primitives. It MUST NOT create a parallel mutable objective or broaden
reviewer repository access.

## 2. Immutable objective, candidate and request

The trusted orchestrator constructs every review request from the original
`TaskObjective`, current `CandidateIdentity`, exact safe-file/review-scope
manifest and current deterministic evidence.

The request binds the stable task/work-unit identities, goal, acceptance
criteria, constraints, expected artifacts, review round, base and candidate
SHAs, candidate and review generations, complete Producer/Fixer contributor
history, scope/material digests, deterministic gate digest, active protocol and
policy revisions, risk/privacy/quorum decisions, lineage and qualification
registry snapshots, and a unique nonce.

The result binds the canonical request digest and all freshness-critical
digests. Mutable PR/Issue text is transport context, not canonical task state.
The exact fields and canonical digest rules are defined in protocol sections
3-10.

## 3. Freshness is more than candidate SHA

Candidate and review generations are monotonic and never reused. Every accepted
Producer/Fixer publication or active base/head binding change advances the
candidate generation. Every new campaign, gate rerun, or objective/scope/policy/
privacy/quorum/registry change advances the review generation.

The orchestrator computes staleness from trusted state. It never accepts a
model-provided `current` flag.

Consequences include:

- B1/H -> B2/H invalidates the B1/H vote;
- H1 -> H2 -> H1 does not revive the earlier H1 vote;
- a new gate digest under the same SHA requires a new campaign;
- a changed objective, scope, privacy/risk/quorum decision or registry snapshot
  invalidates prior results;
- a result copied to another request fails nonce, receipt and request-digest
  binding.

## 4. Authenticated identity and trusted ingestion

Claimed provider/model/family/qualification strings are untrusted. Only the
orchestrator emits a trusted identity envelope after correlating an
authenticated adapter session with the exact nonce, input digest, invocation,
provider-returned actual identity and immutable execution receipt.

Requested and actual model identities are recorded separately. Unexpected
fallback never inherits the requested model's qualification. An unverifiable or
unregistered actual identity is diagnostic only and cannot contribute a P0/P1
vote.

A GitHub comment or model JSON cannot become a vote merely by containing valid
field names. Only trusted ingestion of the exact live invocation can append a
result envelope to the canonical ledger. Protocol sections 8-10 define the
mandatory identity and result fields.

## 5. Complete contributor lineage and independence

Every Producer and Fixer that contributed to the current candidate remains in
an ordered contributor history. Independence is evaluated against the complete
history, not only the latest Fixer.

The versioned lineage registry maps provider principals, serving backends,
foundation models/revisions, aliases, hosted copies and derivatives/fine-tunes
to canonical foundation equivalence classes. Two hosts serving the same
foundation class cannot count as two independent families. A derivative remains
in its foundation root's class unless a later independently reviewed rule proves
otherwise.

A counting reviewer must have a known trusted actual foundation class that is
different from every contributor and every other vote used for quorum. Unknown,
conflicting or ambiguous lineage is `NON_INDEPENDENT` and fails closed.

Thus `Gemini Producer -> Qwen Fixer -> Gemini Reviewer` does not make the final
Gemini review independent.

## 6. Trusted policy derivation and quorum floors

Risk, privacy/egress, security requirements, reviewer class and quorum are
derived by a versioned trusted policy engine from the immutable task objective,
candidate change manifest, data/privacy manifest and subsystem rules.

Producer/Fixer/Reviewer input may request escalation but can never reduce
protection. Ambiguity resolves to the strictest applicable policy; unresolved
privacy ambiguity denies cloud egress.

Non-reducible V1 floors are:

- P0: deterministic gates and at least two qualified `STRONG_P0` reviewers from
  distinct independent foundation equivalence classes;
- P1: deterministic gates and at least two qualified `STRONG_P1` reviewers from
  distinct independent foundation equivalence classes;
- P2: deterministic gates and at least one qualified independent reviewer;
- P3: explicit repository/subsystem policy with exact evidence binding.

Subsystem policy may require more, never less. Changes to risk, privacy,
lineage, qualification or quorum policy are evaluated under the previous
approved policy until independent review and Owner authorization activate the
new revision.

## 7. Reviewer qualification and bootstrap

Reviewer qualification uses both public regression fixtures and separately
sealed held-out fixtures. Public R001 remains valuable regression evidence but
cannot by itself establish Strong P1/P0 capability.

Normal registry promotion requires current authenticated identity, exact
benchmark/harness/custody digests, repeated blinded trials, mandatory hidden
defect recall, false-positive controls, prompt-injection/privacy tests and
independent review.

The initial registry does not bootstrap itself. A one-time `BOOTSTRAP_V1`
ceremony pins the exact repository/harness/configuration, independently inspects
the harness, runs at least two externally authenticated reviewer identities from
distinct provider/foundation lineages against public and sealed fixtures,
records immutable evidence, obtains explicit Owner seed authorization and
appends `BOOTSTRAP_COMPLETE` as the ledger genesis transition.

After `BOOTSTRAP_COMPLETE`, normal Mesh policy is mandatory and bootstrap cannot
silently reopen. Reopening is a P0 governance event with explicit Owner
authorization and an append-only new epoch; it never erases prior evidence or
authorizes deployment. The qualification policy defines the implementable
bootstrap states and transition guards.

## 8. Append-only canonical evidence

GitHub is the initial asynchronous queue and notification bus, not canonical
quorum state. Canonical requests, identities, results, qualification changes,
finding transitions and quorum decisions live in an Owner-private,
content-addressed append-only ledger.

Every record binds a monotonic sequence, previous ledger head, payload and
record digests, request identity, authenticated actor/ingestion receipt,
idempotency key, type and timestamp. Records are never edited in place;
revocation or correction appends a tombstone. Duplicate delivery cannot add a
vote. Continuity failure blocks quorum as `BLOCKED_LEDGER_RECONCILIATION`.

GitHub summaries point to immutable record digests. Editing or deleting a
comment cannot rewrite historical quorum. The stated guarantee is
tamper-evidence inside the Owner-private orchestrator/storage trust boundary;
this ADR does not overclaim that Git, GitHub or hashing defeats a compromised
trusted host.

## 9. Findings survive candidate changes

Every material finding has a stable ID and append-only lifecycle:

`OPEN -> REPAIR_PROPOSED -> VERIFIED_CLOSED`

with evidence-governed `DISMISSED` and `REOPENED` transitions.

BLOCKING/HIGH findings remain inherited by descendant candidate generations
even after their source review result becomes stale. A later PASS cannot erase
them. Repair proposal binds the exact Fixer/candidate/evidence; closure or
dismissal requires the active policy's independent verification set. A
Producer/Fixer cannot close its own finding by assertion.

Where feasible, closure evidence is a regression test, reproducer, invariant,
static violation or exact call-path/state-transition proof. Protocol section 14
defines transition guards.

## 10. Quorum and disagreement

The quorum engine counts only current, authenticated, qualified, privacy-
permitted, lineage-independent, non-duplicated results from the exact active
campaign and valid ledger.

All required evidence must be true simultaneously. Required family votes alone
are insufficient if gates, policy/registry bindings or finding state are not
current.

A model PASS is evidence, not truth or authority. A material finding cannot be
outvoted by collecting unverified PASS results. Disagreement enters finding
verification and remains blocking until the finding is independently verified
closed or dismissed.

## 11. Bounded repair and reviewer capacity

Every repair creates a new candidate generation, reruns gates and starts a new
review generation. Versioned policy sets ceilings for generations, repairs per
finding, review/provider invocations, time/cost budget, repeated state digests
and oscillation cycle length.

Exceeding a ceiling or revisiting a detected cycle transitions to
`BLOCKED_FIXER_CONVERGENCE`; the system does not loop indefinitely or lower
standards.

If the only missing condition is temporary qualified independent reviewer
capacity, state is `WAITING_FOR_INDEPENDENT_REVIEW`. The orchestrator may retry
or select another already-qualified independent family. It cannot substitute
same-family review, weaken privacy, spend unapproved paid capacity or ask the
Owner to shuttle artifacts.

## 12. Prompt injection, privacy and authority

Repository contents, PR/Issue text, comments, review bundles and model findings
are untrusted reviewed material. They cannot instruct the orchestrator or a
reviewer to change policy, mark PASS, disclose secrets, expand permissions or
perform mutations.

Cloud reviewers remain subject to existing PUBLIC/RESTRICTED/PRIVATE policy.
PRIVATE material is denied unless separately authorized by policy; RESTRICTED
material uses approved minimization/sanitization. Provider outage never
overrides egress policy.

The Mesh may prepare authorized candidates, tests, reviews and bounded repairs.
It does not itself authorize:

- merge to protected/stable branches;
- deployment, runtime activation or service restart;
- production alias/registry promotion;
- paid provider usage without pre-approval;
- destructive cleanup;
- external publication;
- privilege expansion.

## 13. Owner gate

`OWNER_GATE_READY` is computed by the orchestrator only when the current task,
candidate, generations, deterministic gates, policy and registry snapshots,
ledger continuity, contributor provenance, qualified independent quorum,
finding lifecycle and privacy/security evidence are simultaneously satisfied.

Owner authorization remains required for protected high-risk merge, activation
and deployment gates. Normal Owner involvement is final authorization, not
routine copying between AI systems. Reconciliation/recovery gates remain
explicit exceptional Owner actions.

## 14. Implementation sequence

G0 is tracked by Issue #32:

1. G0-A implements the existing-Supervisor extension records, canonical
   encoding, trusted policy decisions, request/result/identity envelopes,
   generations, lineage snapshot and stale/replay rejection.
2. G0-B implements `BOOTSTRAP_V1`, public/sealed qualification custody,
   authenticated evidence and qualification registry transitions.
3. G0-C adds the first additional reviewer adapter with verified actual-model
   identity and existing privacy gates.
4. G0-D implements the append-only ledger continuity checks, finding lifecycle
   and deterministic quorum engine.
5. G0-E implements bounded Fixer/re-review convergence controls.
6. G0-F may add an optional ChatGPT strong/tie-break lane only when it runs
   without Owner artifact shuttling and without an OpenAI API requirement.

PR #31 remains Draft, frozen and unactivated while the hardened review
architecture is implemented and used to review its then-current exact candidate.

## 15. Correction traceability

- A1 immutable task provenance: protocol sections 4 and 7.
- A2 replay/staleness: protocol sections 5, 7, 9 and 10.
- A3 authenticated provenance: protocol sections 8 and 9.
- A4 lineage/double counting: protocol sections 6 and 11.
- A5 P1 quorum floor: protocol section 12.
- A6 downgrade prevention: protocol section 12.
- A7 circular bootstrap: this ADR section 7 and qualification policy section 10.
- A8 benchmark leakage: qualification policy sections 4-7.
- A9 durable evidence: protocol section 13.
- A10 finding lifecycle: protocol section 14.

## Consequences

Benefits:

- immutable task and candidate intent cannot silently shrink;
- stale/replayed results and forged identity claims fail closed;
- multi-provider aliases cannot manufacture independent-family quorum;
- public fixtures cannot alone qualify a reviewer;
- findings persist until evidence-governed closure;
- provider outages pause rather than weaken the pipeline;
- the Owner is removed from routine artifact transport.

Costs:

- additional orchestrator schemas, counters and ledger state;
- authenticated adapter and actual-model identity work;
- sealed benchmark custody and qualification maintenance;
- more review latency when independent capacity is unavailable;
- explicit reconciliation when provenance or ledger continuity is ambiguous.

These costs are accepted because silent false-PASS or forged-quorum behavior on
automatic execution, security and privacy changes is more damaging than
fail-closed waiting.
