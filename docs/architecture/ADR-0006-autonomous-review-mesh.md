# ADR-0006: Autonomous Review Mesh

Status: ACCEPTED / IMPLEMENTATION QUEUED

Date: 2026-08-30

Owner approval: architecture approved in project conversation before G0 implementation.

Tracking: Issues #32, #33, #34. Related: #14, #15, #24, PR #31.

## Context

The platform is intended to reduce marginal AI cost and reduce Owner involvement by allowing one task to progress through implementation, verification, review, repair and re-review automatically. Requiring the Owner to copy local model output into a separate chat for review would preserve the old multi-window/manual-shuttle workflow and would not satisfy that product goal.

The existing review path also has an independence weakness. A provider may become the only available producer when Codex quota is unavailable and local Qwen is unavailable or unsuitable. Letting that same provider family satisfy the only required review creates a same-family self-review loop.

PR #31 provided concrete evidence. Gemini 3.6 independently reviewed exact candidate `a94fd5886a12c744c0e7ccd48cf7ea31124968f2` and returned PASS with zero findings. Subsequent semantic verification found a BLOCKING planner-to-runtime workload TOCTOU gap: workload could change after planning and before runtime reuse/start, while the execution path did not re-observe workload plus qualification evidence at the final boundary. The candidate was correctly kept out of Owner gate and later fixed on `cab62d8526b56f20ac49c36b27accef0877d774e`.

Therefore no single reviewer model, model size, provider brand or PASS verdict is sufficient proof of correctness.

## Decision

Adopt an Autonomous Review Mesh controlled by a deterministic orchestrator.

Canonical flow:

`TaskSpec -> Producer -> candidate SHA -> deterministic gates -> review queue -> qualified independent reviewers -> finding verification -> automatic fixer -> new candidate SHA -> repeat gates/review -> quorum -> Owner gate`

LLMs supply proposals, findings and repairs. Deterministic state machines decide whether evidence is current and sufficient to advance.

## 1. Reviewer independence

Every candidate records the producer provider and model family.

A review vote counts as independent only when the reviewer satisfies the configured family-separation policy from the producer and from any other vote required by quorum.

A producer may still self-review for diagnostics, but that vote is marked `NON_INDEPENDENT` and cannot satisfy an independent-review requirement.

Provider/model-family identity is explicit registry metadata. A self-declared role such as `reviewer` does not establish independence.

## 2. Reviewer qualification

A reviewer is not trusted because it is large, expensive, popular or previously useful.

Reviewers must pass a versioned reviewer-qualification suite before their verdicts count for the risk levels covered by that qualification.

The suite contains known-good and known-defective fixtures, including real regressions discovered in this repository. The first mandatory regression fixture is the PR #31 workload/evidence TOCTOU miss tracked by Issue #34.

Qualification measures at minimum:

- recall of known BLOCKING/HIGH defects;
- false-PASS behavior;
- false-positive behavior on known-good fixtures;
- structured-output/schema compliance;
- exact candidate/scope binding;
- prompt-injection resistance for repository-controlled text;
- privacy/egress behavior where cloud review is involved.

Qualification is bound to provider, model ID/version where available, review protocol version and benchmark version. A provider/model change can require requalification.

## 3. Risk levels and quorum

Initial policy:

### P0

Runtime mutation, security boundaries, credentials, deployment, automatic execution, privilege controls and privacy/egress gates.

Requires:

- deterministic verification gates;
- at least two qualified strong independent-family reviewer votes;
- no unresolved BLOCKING/HIGH finding;
- deterministic finding verification for material disagreement where feasible;
- explicit Owner authorization before merge/activation/deploy.

### P1

Architecture, routing, durable state, review governance, sensitive data handling and shared workflow contracts.

Requires deterministic gates, strong independent quorum and explicit Owner authorization.

### P2

Ordinary bounded feature work.

Requires deterministic gates and at least one qualified independent reviewer unless a stricter subsystem rule applies.

### P3

Mechanical, low-risk and documentation-only work.

May use a lighter review rule when repository governance permits, but exact SHA and deterministic gates still apply.

## 4. Review evidence protocol

Review requests and results are machine-readable durable records.

A review request binds at minimum:

- schema/protocol version;
- repository and PR/task identity;
- base SHA;
- exact candidate SHA;
- producer provider/model family;
- risk level;
- deterministic local gate evidence digest;
- privacy classification;
- required reviewer class/quorum.

A review result binds at minimum:

- schema/protocol version;
- exact candidate SHA;
- reviewer provider/model/family;
- reviewer qualification identity/version;
- independent/non-independent classification;
- verdict;
- structured findings;
- reviewed material/evidence digest where applicable;
- timestamp;
- stale/current status.

If candidate head changes, every previous result for another SHA becomes `STALE` automatically. Stale review evidence can remain for history but cannot advance the new candidate.

## 5. Review verdicts are evidence, not truth

A model PASS never directly authorizes merge, deployment or runtime mutation.

A model finding should be converted where feasible into stronger evidence:

- failing deterministic test;
- minimal reproducer;
- invariant violation;
- exact call-path/state-transition proof;
- static/security policy violation.

Unverified findings remain clearly marked. A reviewer may be wrong in either direction.

## 6. Automatic fixer loop

Confirmed findings may automatically create repair tasks.

The router may choose Codex, local Qwen, Gemini or another eligible producer/fixer according to capability, privacy, quota, resource and cost policy.

Every repair produces a new candidate SHA. The old review quorum becomes stale and the complete required verification/review cycle runs again.

Automatic repair loops are bounded by retry/convergence policy. Repeated failure, oscillation or reviewer-capacity exhaustion transitions to a durable blocked state rather than infinite work.

## 7. Reviewer availability and graceful blocking

The platform must not lower review standards merely because a provider is unavailable.

If required independent reviewer capacity is absent, the candidate transitions to:

`WAITING_FOR_INDEPENDENT_REVIEW`

The orchestrator may retry when capacity returns or select another already-qualified reviewer family. It must not silently substitute same-family self-review for an independent vote.

This preserves autonomy: the pipeline waits automatically rather than asking the Owner to shuttle artifacts between chat windows.

## 8. Reviewer pool

Reviewer implementations are interchangeable adapters behind one protocol.

Potential lanes include:

- Codex when quota/capability is available;
- ChatGPT connected/scheduled/conversation review only when it can operate without Owner artifact shuttling and without OpenAI API dependency;
- Gemini under existing privacy/egress controls;
- local Qwen when capability/resource state permits;
- additional free-tier or local reviewer families after qualification.

No specific provider is permanently designated the truth source.

Cloud reviewers remain subject to PUBLIC/RESTRICTED/PRIVATE policy and provider readiness. No silent paid cloud usage is authorized.

## 9. GitHub as durable asynchronous bus

GitHub is the initial durable queue and evidence transport for repository engineering tasks:

- candidate branches/PRs carry exact immutable commit identity;
- deterministic gate evidence is attached to the exact candidate;
- review requests/results remain visible to later agents;
- automatic fixers can consume structured findings;
- stale review detection uses the current PR head.

Future MCP or other transports may expose non-Git runtime artifacts, but they must preserve the same protocol and cannot weaken SHA/evidence binding.

## 10. Prompt-injection boundary

Repository contents, PR text, issue comments, generated review bundles and model-authored findings are untrusted review material.

A reviewer must not treat instructions embedded in reviewed artifacts as authority to change review policy, mark a candidate PASS, disclose secrets, expand permissions or perform runtime/repository mutations.

Reviewer system policy and deterministic orchestrator rules outrank reviewed content.

## 11. Authority boundaries

The Review Mesh may prepare candidates, tests, review evidence and repair commits within separately authorized scopes.

It does not itself authorize:

- merge to protected/stable branches;
- deployment or service restart;
- production alias/registry promotion;
- paid provider usage where not pre-approved;
- destructive cleanup;
- external publication;
- privilege expansion.

Those remain explicit subsystem/Owner gates.

## 12. Implementation sequence

G0 is tracked by Issue #32:

1. G0-A reviewer protocol, registry and stale-result rules.
2. G0-B reviewer qualification harness, seeded by Issue #34.
3. G0-C first additional independent free reviewer adapter.
4. G0-D deterministic quorum engine.
5. G0-E bounded automatic fixer/re-review loop.
6. G0-F optional ChatGPT strong/tie-break lane if it can run without Owner artifact shuttling.

PR #31 remains Draft and unactivated while the new review architecture is established and used to review its current exact candidate.

## Consequences

Positive:

- removes the Owner from routine artifact-shuttling;
- avoids dependence on one reviewer model;
- makes quota/provider outages survivable without weakening policy;
- turns discovered reviewer failures into durable benchmark fixtures;
- allows inexpensive/local producers while reserving stronger reviewers for risk-appropriate gates;
- makes review state reproducible and machine-readable.

Costs:

- more orchestration/state code;
- reviewer qualification maintenance;
- potentially slower P0/P1 completion while quorum is unavailable;
- extra inference/review traffic;
- need to manage disagreement and false positives.

These costs are accepted because silent false-PASS behavior on automatic execution/security changes is more damaging than waiting for reviewer capacity.
