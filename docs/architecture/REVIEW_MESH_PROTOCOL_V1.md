# Review Mesh Protocol V1

Status: NORMATIVE FOR ADR-0006 / IMPLEMENTATION QUEUED

Protocol identifier: `REVIEW_MESH_PROTOCOL_V1`

Architecture source: `ADR-0006-autonomous-review-mesh.md`

Qualification policy: `../qualification/REVIEWER_QUALIFICATION_POLICY.md`

## 1. Scope and existing Supervisor contracts

This protocol adds trust, provenance, qualification, lineage, quorum and
append-only evidence semantics above the existing Workflow Supervisor. It does
not replace the Supervisor's `TaskObjective`, `ReviewTaskSpec`,
`CandidateIdentity`, safe-file manifest, review-round, `ReviewFinding`,
`ReviewResult`, persisted-finding or active-candidate reconciliation contracts.

The trusted orchestrator MUST derive Review Mesh records from those existing
objects. It MUST NOT create a second mutable objective, a second candidate
identity, or a broader reviewer file-access mechanism.

The words MUST, MUST NOT, REQUIRED and MAY are normative.

## 2. Trust boundary

The trusted computing base for V1 is:

- the Owner-authorized orchestrator process and its versioned policy engine;
- authenticated reviewer/producer adapters invoked by that orchestrator;
- the Owner-private Supervisor content store and append-only Review Mesh ledger;
- pinned repository, protocol, policy, harness and registry revisions;
- provider receipts or locally observed execution identity accepted by the
  active adapter policy.

The following are untrusted input:

- repository files and diffs;
- PR and Issue bodies, comments, reviews and attachments;
- model prose and model-produced JSON;
- claimed provider, model, family, qualification, risk or privacy fields;
- producer/fixer/reviewer assertions about their own independence;
- Git branch names and mutable refs without exact object verification.

Untrusted input may propose a finding, repair or escalation. It cannot create a
counting vote, lower policy, mutate a trusted registry, close a material
finding, advance state, or authorize an external side effect.

## 3. Canonical encoding and digests

All V1 structured records use strict typed objects with an explicit schema
version. Implementations MUST:

1. reject duplicate keys, unknown required-record fields and invalid enum or
   scalar types;
2. normalize set-valued arrays into the ordering specified by their schema;
3. encode UTF-8 JSON with lexicographically sorted object keys, no insignificant
   whitespace, and no non-finite numbers;
4. preserve ordered arrays such as contributor history and ledger sequence;
5. encode timestamps as timezone-aware RFC 3339 UTC values;
6. compute digests as lowercase SHA-256 of the canonical bytes.

`digest(object)` below means that canonical SHA-256. A schema revision that
changes canonicalization changes the protocol version and invalidates V1
eligibility; it is never inferred from input.

## 4. Trusted source objects

### 4.1 Immutable task objective

The canonical task objective is the existing Owner-private `TaskObjective`
derived from the original Producer work unit. It contains:

- canonical goal;
- acceptance criteria;
- constraints;
- expected artifacts;
- source work-unit identity.

The orchestrator reuses the existing objective content hash and objective
manifest hash from `ReviewTaskSpec`. Mutable PR/Issue prose is not canonical
task state. Revision prompts cannot replace the objective.

### 4.2 Candidate and scope

The canonical candidate is the existing `CandidateIdentity`, including trusted
base commit, candidate commit/tree identity, deterministic diff digest, exact
changed/deleted path manifest and rename/copy provenance.

The canonical review scope is the exact allowed-path set plus the existing
safe-file manifest. Its digest covers every ordered path, content digest, size,
deletion and rename/copy relationship supplied to the reviewer. Omitted,
additional, secret-bearing, binary, stale, symlinked or out-of-scope material
fails review preparation closed.

## 5. Monotonic generations and review campaigns

For each stable task ID the orchestrator persists two unsigned 64-bit
monotonic counters:

- `candidate_generation`: incremented for every accepted Producer/Fixer
  publication or active candidate-binding change, including a base change or a
  branch moving back to a previously seen SHA;
- `review_generation`: incremented only when the campaign-common semantic
  review context changes or the orchestrator explicitly replaces that
  campaign.

Campaign-common semantic changes include:

- candidate/base binding;
- objective or acceptance criteria;
- review scope or reviewed material;
- deterministic gate evidence;
- contributor history;
- protocol/policy revision;
- risk/privacy/quorum decision;
- lineage or qualification registry snapshot;
- benchmark/harness requirement.

A provider timeout, rate limit, transport error, unavailable reviewer or other
failed reviewer invocation does NOT increment `review_generation` when the
campaign-common context is unchanged. Such failures may be retried inside the
same campaign with a new request nonce and a new lane attempt.

Counters never decrement, wrap, or get reused. Counter loss or ambiguity is
`BLOCKED_LEDGER_RECONCILIATION`.

### 5.1 Canonical campaign context

`CAMPAIGN_CONTEXT_V1` is a canonical object containing only fields shared by
every reviewer lane participating in one quorum campaign.

It contains exactly:

- protocol version;
- repository ID;
- stable task ID;
- source work-unit ID;
- review round;
- candidate generation;
- review generation;
- objective content and manifest digests;
- candidate identity digest;
- exact base SHA;
- exact candidate SHA;
- candidate diff digest;
- review-scope manifest digest;
- reviewed-material digest;
- complete contributor-set digest;
- deterministic local-gate evidence digest;
- active protocol/policy revision;
- trusted policy-decision record digest;
- risk level and risk-decision digest;
- privacy class, egress decision and privacy-decision digest;
- required reviewer class;
- canonical quorum-policy digest;
- lineage-registry snapshot digest;
- qualification-registry snapshot digest;
- benchmark/harness policy revision;
- campaign retry-policy digest.

It MUST NOT contain:

- `review_campaign_id` itself;
- `review_work_unit_id`;
- reviewer lane;
- reviewer identity;
- required adapter principal;
- request nonce;
- lane-attempt number;
- request timestamp;
- request expiry.

Therefore campaign identity has no self-reference and is independent of the
individual reviewer lane.

`campaign_context_digest = digest(CAMPAIGN_CONTEXT_V1)`.

`review_campaign_id = "rc1:" + campaign_context_digest`.

The campaign ID is computed only after the complete context object has been
canonicalized. The ID is never an input to its own digest.

### 5.2 Reviewer lanes and retries

Each reviewer invocation receives a lane-specific `REVIEW_REQUEST_V1`.

One lane attempt binds:

- one `review_work_unit_id`;
- one reviewer lane;
- one required authenticated adapter principal;
- one monotonically increasing `lane_attempt`;
- one unique request nonce;
- one request creation/expiry interval.

A failed invocation that produced no accepted review result may be retried
without changing campaign ID or `review_generation`, provided every
campaign-common field is still identical.

The retry receives a new request ID, nonce and lane attempt. Failed attempts
are durable diagnostic evidence but never count as votes.

If campaign-common context changes before the retry, the old campaign becomes
stale and a new `review_generation` and campaign ID are mandatory.

A retry cannot create duplicate votes. One authenticated reviewer execution may
contribute at most one counting result to the campaign, and quorum still
applies the lineage/equivalence rules in sections 10, 11 and 15.

Quorum may combine results only when campaign ID, campaign-context digest,
candidate generation and review generation are identical.

## 6. Complete contributor history

The orchestrator maintains an ordered, append-only contributor history for the
active candidate. Every Producer or Fixer execution that contributed content
to the candidate is recorded, even after another model repairs that content.

Each contributor entry contains:

- contributor role: `PRODUCER` or `FIXER`;
- candidate generation introduced;
- authenticated identity-envelope digest from section 8;
- execution/invocation ID and receipt digest;
- exact input TaskSpec/request digest;
- input candidate identity digest, or `NONE` for the first Producer;
- output candidate identity digest;
- timestamp.

The `contributor_set_digest` covers the complete ordered history. A candidate
cannot discard earlier contributors merely because the last Fixer used another
family. Missing or ambiguous contributor provenance is
`BLOCKED_IDENTITY_RECONCILIATION`; no independent vote counts until reconciled.

## 7. Canonical review request

The trusted orchestrator constructs `REVIEW_REQUEST_V1`; a producer, reviewer,
PR body or comment cannot construct an authoritative request.

The canonical request payload contains exactly these mandatory fields:

- `protocol_version` = `REVIEW_MESH_PROTOCOL_V1`;
- stable `repository_id`, `task_id`, `source_work_unit_id`,
  `review_work_unit_id` and `review_campaign_id`;
- `review_round`, `candidate_generation` and `review_generation`;
- monotonically increasing `lane_attempt` within the selected reviewer lane;
- unique cryptographically random `request_nonce` of at least 128 bits;
- `created_at`, bounded `request_expiry_at` and the campaign retry-policy
  digest;
- objective content digest and objective manifest digest from `TaskObjective`;
- candidate identity digest, exact `base_sha`, exact `candidate_sha` and
  candidate diff digest;
- allowed-path/review-scope manifest digest and reviewed-material digest;
- complete contributor history digest and contributor identity-envelope
  digests;
- deterministic local-gate evidence digest;
- active protocol/policy revision and trusted policy-decision record digest;
- derived risk level and risk-decision digest;
- derived privacy class, egress decision and privacy-decision digest;
- required reviewer class and canonical quorum-policy digest;
- lineage-registry snapshot digest;
- qualification-registry snapshot digest;
- benchmark/harness policy revision required for the reviewer class;
- requested reviewer lane and required authenticated adapter principal.

`request_digest = digest(request_payload)`.

`review_request_id = "rr1:" + request_digest`.

The request record stores the payload, digest and ID. A mismatch is
`INVALID_REVIEW_REQUEST` and cannot be queued.

## 8. Authenticated execution identity envelope

Claimed identity and trusted observed identity are separate objects. Model
output may report a claim, but only the orchestrator can emit an
`IDENTITY_ENVELOPE_V1` after correlating an authenticated adapter session with
the live invocation and its input digest.

The identity envelope contains exactly:

- protocol/schema version and identity-envelope ID;
- authenticated adapter principal and authentication-method/credential-version
  identifier, without secret material;
- provider principal and provider account/tenant scope where relevant;
- requested model ID and requested endpoint/alias;
- provider-returned actual model ID when available;
- actual fallback model and fallback reason, or explicit `NO_FALLBACK`;
- canonical serving backend;
- canonical foundation model, foundation lineage/equivalence class and material
  foundation revision;
- hosted-copy, derivative or fine-tune relationship identifiers;
- local/remote classification and actual egress destination;
- invocation/execution ID and immutable receipt digest;
- request nonce and exact input/reviewed-material digest;
- task/request digest and candidate generation;
- invocation start/completion timestamps;
- privacy/egress decision digest;
- lineage-registry and qualification-registry snapshot digests;
- qualification-evidence digest used for eligibility;
- orchestrator ingestion timestamp and authenticated ingestion receipt digest.

The envelope ID is the digest of the remaining canonical envelope fields.

If the provider cannot prove its actual served identity, or requested/actual
identity differs without a registered verified fallback, the execution is
`UNVERIFIED_IDENTITY`. It may remain diagnostic but cannot count as a P0/P1
vote. An unexpected fallback never inherits the requested model's
qualification. The actual fallback must have its own current lineage and
qualification record or the result is non-counting.

Local executions require an adapter-observed executable/runtime/model identity
bound to the invocation and input digest. A self-reported local model name is
not sufficient.

## 9. Canonical review result and trusted ingestion

The model returns only an untrusted structured payload containing a claimed
verdict and findings.

The orchestrator validates that payload only in the authenticated invocation
created for the exact `REVIEW_REQUEST_V1`. It then constructs a canonical
`REVIEW_RESULT_PAYLOAD_V1`.

### 9.1 Canonical result payload

`REVIEW_RESULT_PAYLOAD_V1` contains exactly these mandatory fields:

- protocol/schema version;
- review request ID and request digest;
- review campaign ID and campaign-context digest;
- review work-unit ID and lane attempt;
- review round, candidate generation and review generation;
- objective content/manifest digests;
- exact base SHA, candidate SHA, candidate identity and diff digests;
- review-scope manifest and reviewed-material digests;
- deterministic local-gate evidence digest;
- active policy, risk-decision, privacy-decision and quorum-policy digests;
- lineage-registry and qualification-registry snapshot digests;
- complete contributor-set digest;
- reviewer identity-envelope digest and qualification-evidence digest;
- invocation ID, execution nonce and provider/adapter execution-receipt digest;
- claimed verdict, normalized structured findings and findings digest;
- invocation completion timestamp;
- raw-result content digest and bounded raw-result storage reference.

The canonical result payload MUST NOT contain:

- its own result digest or result ID;
- trusted-ingestion receipt generated after payload validation;
- ledger sequence;
- ledger record digest;
- any mutable `current`, `stale`, `independent`, `qualified` or `counting`
  status.

`review_result_digest = digest(REVIEW_RESULT_PAYLOAD_V1)`.

`review_result_id = "rrs1:" + review_result_digest`.

`REVIEW_RESULT_ENVELOPE_V1` consists of:

- the immutable canonical result payload;
- `review_result_digest`;
- `review_result_id`.

The digest and ID are derived from the payload and are not included in the
bytes from which their own value is calculated. This removes result
self-reference.

### 9.2 Trusted ingestion and ledger anchoring

After the result digest exists, trusted ingestion creates a separate
`RESULT_INGESTION_V1` payload containing:

- review result ID and digest;
- review request ID and request digest;
- reviewer identity-envelope digest;
- invocation/execution ID;
- execution-receipt digest;
- orchestrator ingestion timestamp;
- authenticated ingestion-receipt digest;
- idempotency key.

That ingestion payload is appended through the generic `LEDGER_RECORD_V1`
procedure in section 13.

The ledger record therefore depends on the already-computed review-result
digest. The review-result digest never depends on a ledger record digest.

The resulting ledger record digest is an external durable anchor/reference for
the result. It is not inserted back into `REVIEW_RESULT_PAYLOAD_V1`, so no
result <-> ledger digest cycle exists.

A trusted read model may expose:

- `review_result_id`;
- `review_result_digest`;
- ingestion ledger-record digest;
- ledger sequence;
- computed eligibility/status.

Those are views over immutable records and MUST NOT alter the canonical result
payload.

The model-produced payload MUST NOT provide authoritative `current`, `stale`,
`independent`, `qualified` or `counting` booleans. Those are computed views of
trusted state.

A GitHub comment or detached JSON object that was not correlated with the
exact live invocation, nonce, receipt, request digest and trusted ingestion
record is `UNTRUSTED_RESULT` and never becomes a vote.

## 10. Staleness and replay rejection

At result ingestion and again at every quorum evaluation, the orchestrator
requires simultaneous equality with active trusted state for:

- request and campaign IDs/digests;
- base and candidate SHAs;
- candidate identity/diff, objective, scope and reviewed-material digests;
- candidate and review generations;
- local-gate evidence digest;
- active policy, risk, privacy and quorum digests;
- contributor-set, lineage-registry and qualification-registry digests;
- reviewer qualification/harness/benchmark revision;
- nonce, invocation and receipt binding;
- non-revoked ledger records.

Any mismatch makes the result `STALE` or `INVALID` and non-counting. Returning
from H2 to an earlier H1 creates a later candidate generation; H1 votes from the
earlier generation never revive. Keeping H while B1 changes to B2 likewise
creates a new binding/generation and invalidates the earlier result. A gate,
scope, objective, privacy, risk, quorum or registry change creates a new review
generation and request. Timestamps or model-provided status cannot override
these rules.

One invocation nonce, execution receipt and result digest can contribute at
most one vote. Duplicate exact delivery returns the existing ingestion record.
A reused idempotency key with different content is
`BLOCKED_LEDGER_RECONCILIATION`.

A failed provider/transport invocation that produced no accepted result does
not stale otherwise-current votes in the same campaign. A retry under unchanged
campaign context uses the same `review_generation` and campaign ID, with a new
request nonce and incremented `lane_attempt`.

A retry MUST NOT reuse the failed invocation receipt or nonce. A failed attempt
cannot later be transformed into a counting result by editing transport
records.

If any campaign-common semantic field changed while waiting to retry, section
5 requires a new review generation and campaign; all prior campaign votes then
remain historical and non-counting for the new campaign.

## 11. Canonical lineage registry and independence

The versioned lineage registry contains, for every eligible identity:

- provider principal;
- serving backend;
- requested and actual model identifiers;
- canonical foundation model and material revision;
- foundation lineage/equivalence-class ID;
- aliases and hosted copies;
- derivative/fine-tune relationships;
- known equivalence and shared-root relationships;
- evidence source, policy revision and approval state.

Aliases, endpoints and hosted copies of the same foundation lineage share one
equivalence-class ID. Derivatives/fine-tunes remain in their foundation root's
class unless a later independently reviewed policy proves a stricter separation
rule; marketing names never establish separation.

A reviewer is independent for a candidate only if all are true:

1. its trusted actual foundation equivalence class is known;
2. that class is absent from every entry in the complete contributor history;
3. that class is absent from every other vote used for the same quorum;
4. its invocation and qualification identity are current under the same
   registry snapshots as the campaign;
5. no policy-defined provider/backend correlation rule disqualifies it.

Unknown, conflicting or ambiguous lineage is `NON_INDEPENDENT`; it never creates
an extra family. Two providers hosting the same foundation class contribute at
most one family vote. Independence is recomputed by the orchestrator at quorum
time, never copied from reviewer prose.

## 12. Trusted policy derivation and non-reducible quorum

The versioned policy engine derives risk, privacy/egress, security gates,
reviewer class and quorum from the immutable TaskObjective, CandidateIdentity
change manifest, data/privacy manifest and applicable subsystem policy.

Producer, Fixer and Reviewer input may request escalation. It MUST NOT reduce a
derived protection. Conflicting or ambiguous classifications resolve to the
strictest applicable risk, privacy and quorum policy; an unresolved privacy
ambiguity denies cloud egress.

V1 floors are:

- P0: deterministic gates plus at least two qualified `STRONG_P0` reviewers
  from distinct independent foundation equivalence classes;
- P1: deterministic gates plus at least two qualified `STRONG_P1` reviewers
  from distinct independent foundation equivalence classes;
- P2: deterministic gates plus at least one qualified independent reviewer;
- P3: the explicit repository/subsystem rule, with exact request/evidence
  binding still mandatory.

Subsystem policy may increase but cannot reduce these floors. A change to
quorum, lineage, qualification, risk or privacy policy is evaluated under the
previous approved policy until independent review and explicit Owner
authorization activate the new revision. A request-supplied lower quorum is
`INVALID_POLICY_DOWNGRADE`.

## 13. Append-only Review Mesh ledger

GitHub is an asynchronous notification/transport surface, not canonical quorum
state. Canonical requests, identity envelopes, results, finding transitions,
qualification changes, policy decisions and quorum decisions live in an
Owner-private append-only ledger.

Every `LEDGER_RECORD_V1` contains:

- protocol/schema and record type;
- monotonic sequence number;
- previous ledger-head digest, or the pinned bootstrap genesis value;
- record payload digest and record digest;
- related request/campaign/task ID;
- authenticated orchestrator/adapter actor provenance;
- ingestion receipt digest;
- idempotency key;
- creation timestamp;
- optional superseded/revoked record digest for tombstone records.

`record_digest` covers the complete header except itself plus the payload
digest. One serialized orchestrator transaction verifies the expected previous
head, assigns the next sequence and commits the record and new head atomically.

Records are never edited or deleted in place. Correction, invalidation,
suspension and revocation append typed tombstone records. Exact duplicate
delivery is idempotent and cannot add a vote. A sequence gap, previous-head
mismatch, digest failure, conflicting idempotency key or missing referenced
record is `BLOCKED_LEDGER_RECONCILIATION`; quorum evaluation stops.

The orchestrator verifies continuity from the pinned genesis digest through the
current head on startup, before ingesting results, and before Owner-gate
eligibility. The ledger is tamper-evident inside the stated Owner-private
orchestrator/storage trust boundary; V1 does not claim that Git, GitHub or a hash
alone defeats a compromised trusted host.

GitHub comments may publish bounded human-readable summaries and immutable
ledger/request/result digests. Editing or deleting a comment cannot modify the
ledger or quorum. A branch/ref change is independently observed and appended as
a new candidate-binding event.

## 14. Material finding lifecycle

Every normalized finding receives a stable ID derived from the originating
result digest, finding ordinal and normalized finding-content digest. Finding
identity survives candidate and review generation changes.

Material severities are `BLOCKING` and `HIGH`. Their states are:

- `OPEN`;
- `REPAIR_PROPOSED`;
- `VERIFIED_CLOSED`;
- `DISMISSED`;
- `REOPENED`.

Allowed transitions are append-only ledger events:

1. new material finding -> `OPEN`;
2. `OPEN` or `REOPENED` -> `REPAIR_PROPOSED` only when a new exact candidate
   generation, Fixer identity and repair-evidence digest are linked;
3. `REPAIR_PROPOSED` -> `VERIFIED_CLOSED` only when the active policy's required
   qualified independent verification set binds the exact repair candidate and
   closure evidence;
4. `OPEN`, `REPAIR_PROPOSED` or `REOPENED` -> `DISMISSED` only when the active
   policy's required independent verification set establishes a false positive
   or non-applicability with exact evidence;
5. `VERIFIED_CLOSED` or `DISMISSED` -> `REOPENED` when a current deterministic
   failure or qualified independent finding proves recurrence or invalidates the
   closure evidence.

Where feasible, closure evidence includes a regression test, reproducer,
invariant, static violation or exact call-path/state-transition proof. When that
is not feasible, the independent verification record MUST state why and bind
the alternative evidence required by policy.

A Producer/Fixer cannot close or dismiss its own material finding by assertion.
A PASS result never closes a finding implicitly. Staling the review result that
introduced a finding does not stale the finding. Every descendant candidate
inherits all `OPEN`, `REPAIR_PROPOSED` and `REOPENED` material findings until an
allowed transition closes or dismisses them.

## 15. Quorum evaluation and disagreement

The deterministic quorum engine counts a result only when:

- section 10 says it is current;
- trusted ingestion and identity validation passed;
- reviewer qualification covers the active protocol/risk/benchmark revisions;
- privacy/egress policy permits the execution;
- section 11 says it is independent from all contributors and other counted
  votes;
- it has not been revoked, duplicated or previously consumed for another
  campaign;
- ledger continuity is valid.

Quorum is satisfied only when all required current evidence is simultaneously
true: deterministic gates pass, the family floor is met, required security and
privacy evidence passes, and no inherited material finding is `OPEN`,
`REPAIR_PROPOSED` or `REOPENED`.

Reviewer PASS is evidence, not authority. A material finding cannot be outvoted
by accumulating PASS results. Disagreement creates or updates a finding and
routes it to deterministic verification and the section 14 lifecycle. Until it
is verified closed or dismissed, the candidate remains blocked from quorum.

If the only missing condition is temporary qualified independent reviewer
capacity, state is `WAITING_FOR_INDEPENDENT_REVIEW`. Identity, ledger, privacy,
policy or provenance ambiguity uses its specific blocked-reconciliation state;
it is not misreported as capacity waiting.

## 16. Bounded Fixer loop

The active policy fixes immutable ceilings for:

- candidate generations per task;
- repair attempts per stable finding ID;
- total review generations and provider invocations;
- wall-clock and approved cost budgets;
- recurrence count for an identical finding signature;
- repeated candidate/context/finding-set digests;
- maximum detected oscillation cycle length.

Every repair receives a new candidate generation, reruns deterministic gates and
creates a new review generation. Repeating a prior candidate/context/finding
state, exceeding any ceiling, or alternating within the configured cycle window
transitions to `BLOCKED_FIXER_CONVERGENCE`. No loop weakens gates, erases
findings, spends unapproved paid capacity or asks the Owner to shuttle artifacts.

## 17. Owner-gate and authority boundary

`OWNER_GATE_READY` is a computed state, not a model verdict. It requires in one
trusted transaction:

- current request/candidate/policy/registry/gate bindings;
- valid ledger continuity;
- complete contributor provenance;
- required current qualified independent quorum;
- no unresolved inherited material finding;
- all required security/privacy evidence;
- no active reconciliation/fixer/capacity block.

Owner authorization remains required for protected high-risk merge, activation
and deployment gates. Review Mesh state never authorizes merge, deployment,
service restart, production registry promotion, paid usage, destructive cleanup,
external publication or privilege expansion. Those actions require their own
explicit authorized subsystem transition.

Routine artifact copying between AI conversations is not a normal Owner duty.
The orchestrator persists and transports canonical records automatically; the
Owner normally appears only at the final authorization gate or an explicit
reconciliation/recovery gate.

## 18. Required state outcomes

Implementations use these fail-closed outcomes at minimum:

- `INVALID_REVIEW_REQUEST`;
- `UNTRUSTED_RESULT`;
- `UNVERIFIED_IDENTITY`;
- `NON_INDEPENDENT`;
- `STALE`;
- `INVALID_POLICY_DOWNGRADE`;
- `WAITING_FOR_INDEPENDENT_REVIEW`;
- `BLOCKED_IDENTITY_RECONCILIATION`;
- `BLOCKED_LEDGER_RECONCILIATION`;
- `BLOCKED_FIXER_CONVERGENCE`;
- `OWNER_GATE_READY`.

Only the deterministic orchestrator transitions these states. Model text,
GitHub state labels and transport availability cannot override them.
