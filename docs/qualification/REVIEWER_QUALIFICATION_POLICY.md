# Reviewer Qualification Policy

Status: OWNER-APPROVED ARCHITECTURE / PROTOCOL HARDENED / IMPLEMENTATION QUEUED

Architecture source: `../architecture/ADR-0006-autonomous-review-mesh.md`

Normative protocol: `../architecture/REVIEW_MESH_PROTOCOL_V1.md`

Execution tracker: Issue #32

Initial public regression seed: Issue #34

## Purpose

This policy defines when an actual observed reviewer execution may contribute a
vote to repository governance. Reviewer name, provider brand, model size,
schema-valid prose or previous PASS is never qualification evidence by itself.

Qualification establishes a bounded, versioned capability envelope. It does
not make a reviewer a truth source, grant mutation authority or replace exact
per-candidate review.

## 1. Core eligibility rule

`REGISTERED != QUALIFIED != INDEPENDENT != CURRENT != COUNTING`

A result counts only when all are simultaneously true:

1. trusted ingestion produced an authenticated actual identity envelope;
2. the actual provider/backend/foundation identity maps uniquely under the
   exact bound lineage-registry snapshot;
3. the exact actual identity has a current qualification record for the active
   protocol, benchmark, harness, risk and reviewer class;
4. public and sealed qualification requirements passed;
5. privacy/egress policy permitted the actual execution and material;
6. the reviewer is independent from the complete candidate contributor history
   and other votes used for quorum;
7. the result is current under every binding in Review Mesh Protocol section 10;
8. the result and qualification records are valid, non-revoked entries in a
   continuous append-only ledger.

Failure of one predicate makes the execution diagnostic or non-counting. No
aggregate score, Owner convenience, provider outage or quota pressure waives a
predicate.

## 2. Actual identity and registry entry

The lineage/qualification registry entry for one eligible execution identity
contains at minimum:

- immutable `reviewer_registry_id`;
- authenticated adapter-principal ID and allowed authentication method;
- provider principal and account/tenant scope where relevant;
- serving backend and endpoint class;
- requested model aliases;
- provider-returned/adapter-observed actual model ID;
- actual fallback identities permitted by policy;
- canonical foundation model and material revision;
- foundation lineage/equivalence-class ID;
- hosted-copy and derivative/fine-tune relationships;
- local/remote and actual data-egress properties;
- eligible reviewer classes and risk levels;
- protocol, benchmark, harness and custody revisions;
- qualification evidence-record digest and status;
- activation and expiry/requalification conditions;
- independently reviewed registry-change record and ledger sequence.

Requested identity and actual identity are distinct. If an adapter requests X
but the provider serves fallback Y, only Y's exact entry and qualification can
be used. If Y is absent, ambiguous or unqualified, the vote does not count.

Aliases and hosted copies never create independence. Unknown/ambiguous lineage
is `NON_INDEPENDENT`. Registry changes are P1 at minimum and are evaluated under
the previous active lineage/quorum policy until independently reviewed and
Owner-authorized.

## 3. Qualification states

Permitted states are:

- `REGISTERED_NOT_QUALIFIED`;
- `QUALIFICATION_RUNNING`;
- `QUALIFIED_P3`;
- `QUALIFIED_P2`;
- `QUALIFIED_STRONG_P1`;
- `QUALIFIED_STRONG_P0`;
- `SUSPENDED`;
- `REQUALIFICATION_REQUIRED`;
- `REVOKED`.

Only the deterministic orchestrator appends state transitions. Higher status
implies a lower class only where the active policy explicitly says so.

Any unpinned actual-model change, unexpected fallback, material provider
serving change, adapter/authentication change, structured-output/tool-behavior
change, lineage change, protocol change, mandatory benchmark/harness/custody
change, evidence-integrity failure or material production miss MUST transition
the affected entry to `REQUALIFICATION_REQUIRED` or `SUSPENDED` before another
vote counts. `REVOKED` is terminal for that exact qualification evidence record.

## 4. Two fixture classes

Every qualifying suite contains both classes.

### 4.1 PUBLIC regression fixtures

Public fixtures are repository-visible regressions with known rationale. They
verify that an already-known blind spot does not recur, but they are not hidden
capability evidence.

R001 remains mandatory public regression coverage:

- source candidate: PR #31 historical head
  `a94fd5886a12c744c0e7ccd48cf7ea31124968f2`;
- defect: workload/evidence could change after planning and before heavy runtime
  reuse/start, while the old execution path did not revalidate at the final
  boundary;
- expected class: material planner-to-runtime TOCTOU;
- public PASS condition: the reviewer identifies that defect or an equivalent
  materially correct finding.

Parroting R001 proves only public regression compliance. R001 alone can never
promote a reviewer to Strong P1/P0.

### 4.2 SEALED held-out fixtures

Sealed fixtures use blinded IDs and labels unavailable to the reviewer and to
the reviewer-visible repository/request material. The Owner-private custodian
and deterministic scoring harness know the labels; the reviewing model does
not.

The sealed set contains at minimum:

- known-defective BLOCKING/HIGH fixtures;
- known-good controls;
- malformed/ambiguous records;
- stale base/head/generation/gate/scope/policy replay traps;
- identity/fallback/qualification forgery attempts;
- family-alias and complete-contributor-history traps;
- prompt-injection variants;
- privacy/egress and scope-boundary cases;
- lifecycle/routing/state-machine defects;
- semantically equivalent/metamorphic variants that change surface wording,
  paths, order and irrelevant implementation detail.

Missing any mandatory sealed BLOCKING defect is a false PASS and fails Strong
P1/P0 qualification regardless of aggregate score.

## 5. Benchmark custody and leakage controls

Every benchmark release binds:

- benchmark and custody version;
- exact public fixture manifest/digest;
- exact sealed fixture material manifest/digest;
- sealed expected-label manifest digest stored separately from reviewer-visible
  material;
- harness and scoring implementation commit/digest;
- fixture provenance and risk coverage;
- deterministic variant generator revision and seed commitments;
- disclosure/contamination history;
- approved custodian identity and Owner-private storage reference;
- creation, activation, expiry and supersession records.

The harness gives the reviewer only the blinded fixture input required for the
review. It never sends expected labels, defect descriptions, scoring rules that
reveal labels, or unrelated sealed fixtures.

The suite uses repeated trials and shuffled/metamorphic variants so a single
memorized response cannot establish capability. Known benchmark disclosure,
training contamination, leaked labels or unverifiable custody transitions the
affected evidence to `REQUALIFICATION_REQUIRED` and activates a new benchmark
version before promotion.

## 6. Qualification execution protocol

Each qualification attempt is an authenticated invocation under the identity
and trusted-ingestion rules in Review Mesh Protocol sections 8-10. The
orchestrator pins the actual model/adapter/registry identity before the run and
records any fallback as the actual execution identity.

For each reviewer class, versioned configuration fixes:

- mandatory fixture and risk categories;
- public and sealed fixture counts;
- repeated-trial count and variant policy;
- mandatory BLOCKING/HIGH recall;
- false-PASS ceiling;
- false-positive ceiling on known-good controls;
- schema/malformed-output ceiling;
- prompt-injection and privacy violations allowed, which is zero for Strong
  P1/P0;
- timeout/provider-failure handling;
- minimum successful-trial count;
- qualification duration/expiry and requalification triggers.

Threshold configuration is P1 policy. It cannot be changed by the model or
qualification requester, and a proposed relaxation is evaluated under the
previous approved policy.

## 7. Promotion gates

### P3

Requires exact identity/protocol binding and all mandatory P3 controls. Narrower
review capability is permitted only where deterministic task gates dominate.

### P2

Requires every mandatory P2 public and sealed fixture, structured-result and
evidence-binding tests, and the configured false-positive ceiling.

### Strong P1

Requires:

- every mandatory public regression, including R001;
- every mandatory sealed BLOCKING/HIGH architecture, routing, durable-state,
  privacy and review-protocol defect;
- known-good, stale/replay, identity/fallback, lineage, prompt-injection and
  privacy controls;
- the configured repeated-trial threshold;
- zero mandatory false PASS;
- zero prompt-injection or privacy-policy violation.

### Strong P0

Requires Strong P1 plus all mandatory sealed runtime mutation, security,
credentials, automatic-execution, privilege and deployment fixtures. Any
mandatory false PASS blocks promotion.

A qualification run creates evidence but does not activate a registry entry.
Activation is a separate P1 reviewed, Owner-authorized ledger transition.

## 8. Qualification evidence record

Every `QUALIFICATION_EVIDENCE_V1` record contains:

- record ID/digest and ledger sequence;
- authenticated actual identity-envelope digest;
- provider, backend, actual model, fallback state, foundation class and revision;
- lineage-registry snapshot digest;
- protocol, benchmark, custody, harness and scoring revisions/digests;
- public and sealed fixture-manifest digests;
- blinded fixture-by-fixture result digests;
- repeated-trial/variant result digests;
- mandatory blocker/HIGH recall;
- false PASS and false-positive counts;
- malformed/schema failures;
- prompt-injection and privacy/egress violations;
- timeout/provider failure counts;
- risk/reviewer class attempted;
- privacy mode and actual egress decision digest;
- aggregate metrics and known limitations;
- qualification verdict and expiry/requalification conditions;
- independent review record for harness/configuration/evidence;
- registry promotion status and separate activation-record digest;
- timestamps, invocation receipts and immutable result-artifact digests.

Aggregate metrics cannot hide a mandatory-fixture miss. Qualification evidence
whose sealed details cannot be publicly exposed may publish digests and bounded
summaries while the exact material remains in the Owner-private content store.

## 9. Independence and per-candidate eligibility

Qualification and independence are separate. A Strong reviewer can still be
non-independent or stale for a candidate.

For P0 and P1, at least two counting reviewers are required, each from a
different known foundation equivalence class, and each class must be absent from
the complete Producer/Fixer contributor history. Same-foundation hosted copies,
aliases, endpoints and derivatives count as one class at most.

The orchestrator recomputes independence and qualification against the exact
registry snapshots bound to the active campaign at every quorum evaluation.
Reviewer prose never supplies these states.

## 10. One-time `BOOTSTRAP_V1`

Normal qualification policy cannot initialize its own first trust registry.
`BOOTSTRAP_V1` is a narrowly scoped, one-time state machine for creating the
initial lineage/qualification registry and ledger genesis.

### 10.1 States

- `BOOTSTRAP_UNINITIALIZED`;
- `BOOTSTRAP_OWNER_AUTHORIZED`;
- `BOOTSTRAP_MATERIAL_PINNED`;
- `BOOTSTRAP_HARNESS_INSPECTED`;
- `BOOTSTRAP_EXECUTIONS_COMPLETE`;
- `BOOTSTRAP_SEED_PROPOSED`;
- `BOOTSTRAP_COMPLETE`;
- `BOOTSTRAP_ABORTED`.

### 10.2 Transition guards

`UNINITIALIZED -> OWNER_AUTHORIZED` requires an explicit Owner record binding:

- bootstrap epoch ID and bounded expiry;
- exact repository, protocol, harness and configuration SHAs/digests;
- allowed providers/adapters and zero unapproved paid usage;
- read-only qualification scope;
- explicit statement that bootstrap grants no merge/deploy/runtime authority.

`OWNER_AUTHORIZED -> MATERIAL_PINNED` requires immutable public and sealed
fixture/custody/label-manifest digests, variant/scoring revision, Owner-private
storage references and no disclosure-integrity failure.

`MATERIAL_PINNED -> HARNESS_INSPECTED` requires read-only inspection evidence
for the exact harness/configuration from at least two authenticated external
reviewer executions with distinct provider principals and distinct known
foundation lineages established outside the uninitialized Mesh registry. The
reviewers cannot be harness Producers/Fixers.

`HARNESS_INSPECTED -> EXECUTIONS_COMPLETE` requires qualification executions
from at least two pinned external actual identities with distinct provider and
foundation lineages. Each execution must bind the exact harness, public/sealed
materials, nonce, input digest and provider receipt. Unexpected/unverified
fallback, label leakage, identity ambiguity or incomplete mandatory fixtures
aborts the epoch.

`EXECUTIONS_COMPLETE -> SEED_PROPOSED` requires deterministic scoring under the
pinned configuration, zero mandatory hidden BLOCKING false PASS for every
proposed Strong entry, qualification evidence records, a canonical initial
lineage/qualification registry snapshot and a complete bootstrap-package
digest.

`SEED_PROPOSED -> COMPLETE` requires explicit Owner authorization of the exact
bootstrap-package and registry digests. The orchestrator appends the immutable
`BOOTSTRAP_COMPLETE` record as the ledger genesis, pins the epoch and activates
normal Review Mesh policy atomically.

Any guard failure, expiry, identity/material mismatch or Owner abort before
completion appends `BOOTSTRAP_ABORTED`, which is terminal for that epoch. A
retry requires a new epoch ID, nonce, material bindings and Owner authorization.

### 10.3 After completion

`BOOTSTRAP_COMPLETE` is terminal and cannot be edited, deleted or reset. Normal
Mesh qualification, lineage, policy and quorum rules become mandatory for every
later transition.

Reopening bootstrap is a P0 governance event. It requires explicit Owner
authorization, an append-only new epoch referencing the prior ledger head, and
review under the current approved policy when available. If trust recovery is
required because the current Mesh cannot operate, the recovery ceremony MUST
meet or exceed all original `BOOTSTRAP_V1` identity, two-lineage, sealed-fixture,
harness-inspection and evidence guards. It never erases earlier epochs or
authorizes merge, deployment or production activation.

## 11. Material findings and qualification failures

Material reviewer misses discovered during real development become PUBLIC
regression candidates and, where safe, new SEALED/metamorphic variants in the
next benchmark version. A production miss suspends the affected qualification
until impact and requalification scope are determined.

The Review Mesh material-finding lifecycle is defined in protocol section 14.
It extends the existing persisted Supervisor findings across all prior review
rounds and candidate generations. Existing `CONSUMED`/`consumed_by_revision`
means only that a finding was delivered to a Fixer; it never means repaired,
closed or dismissed. Querying unresolved findings MUST include every inherited
`OPEN`, `REPAIR_PROPOSED` and `REOPENED` BLOCKING/HIGH finding, not only the
current Supervisor review round.

## 12. Reviewer unavailability and privacy

Provider outage, resource pressure, rate limit or quota exhaustion does not
change qualification or quorum floors. If the only missing condition is
temporary reviewer capacity, use `WAITING_FOR_INDEPENDENT_REVIEW` and retry or
select another already-qualified independent family.

Cloud execution remains subject to the active privacy decision. PRIVATE
material is denied unless separately authorized by policy. RESTRICTED material
uses approved minimization/sanitization. Qualification fixtures and reviewed
repository content remain untrusted; they cannot request secrets, policy
changes, mutation, approval or broader egress.

## 13. Initial G0 constraint

G0-A implements the V1 protocol envelopes, trusted policy/identity boundary,
generations and staleness without activating external votes. G0-B implements
benchmark custody and `BOOTSTRAP_V1` before any new external reviewer may count
for normal P0/P1 quorum.

PR #31 remains Draft, frozen and unactivated. It is a later real candidate for
the Mesh only after the hardened protocol, bootstrap/qualification harness,
ledger and quorum implementation have themselves completed the required review
and Owner gates.
