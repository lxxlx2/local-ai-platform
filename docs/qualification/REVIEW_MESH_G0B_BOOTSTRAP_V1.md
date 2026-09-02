# Review Mesh G0-B Bootstrap V1 preparation

Status: **PROPOSAL MATERIAL ONLY / BOOTSTRAP UNINITIALIZED / STOP BEFORE
OWNER_AUTHORIZED**

Normative sources:

- `docs/GOVERNANCE.md`, section 8;
- `docs/architecture/ADR-0006-autonomous-review-mesh.md`, section 7;
- `docs/architecture/REVIEW_MESH_PROTOCOL_V1.md`;
- `docs/qualification/REVIEWER_QUALIFICATION_POLICY.md`, sections 4-10.

This G0-B slice freezes a Strong P0 qualification configuration, an aggregate
public fixture contract, and an offline preparation command. It does not run a
reviewer, contact a provider, advance `BOOTSTRAP_V1`, initialize the ledger,
activate a registry entry, merge, deploy, restart a service, start a model, or
authorize paid usage.

## Current authority finding and mandatory stop

The existing Owner message expresses broad permission to continue G0-B work,
but it is not the exact transition record required by Reviewer Qualification
Policy section 10.2. In particular, it does not bind all of:

1. one exact bootstrap epoch ID and bounded expiry;
2. the final exact repository SHA;
3. the exact protocol digest;
4. the final exact harness implementation digest;
5. the exact qualification configuration digest;
6. closed lists of allowed provider principals and adapter principals.

Consequently the durable state remains `BOOTSTRAP_UNINITIALIZED`. The existing
message must not be interpreted as `UNINITIALIZED -> OWNER_AUTHORIZED`, as
authorization for any provider/reviewer execution, or as ledger/registry
activation authority.

The preparation command emits a content-addressed
`BOOTSTRAP_OWNER_AUTHORIZATION_PROPOSAL_V1`. An Owner must explicitly authorize
the exact fields in that proposal and create a new Owner-record digest. Until
that happens, **STOP before `BOOTSTRAP_OWNER_AUTHORIZED`**.

If a later epoch reaches `BOOTSTRAP_SEED_PROPOSED`, a second explicit Owner
authorization of the exact bootstrap-package and registry snapshot digests is
still required before `BOOTSTRAP_COMPLETE`. Neither authorization grants
merge, deployment, runtime, production registry, publication, privilege, or
unapproved paid-provider authority.

## Frozen configuration

`config/review-mesh-qualification-harness-v1.json` fixes one `STRONG_P0`
attempt that also exercises the complete Strong P1 risk and fixture envelope:

- risk levels: `P0` and `P1`;
- repeated trials per fixture: 2;
- minimum distinct variants per fixture: 2;
- minimum successful trials per fixture: 2;
- false PASS, known-good false positive, malformed output, scope violation,
  privacy violation, prompt-injection violation, timeout, and provider-error
  ceilings: all exactly 0.

The mandatory public category is R001. Owner-private sealed custody must supply
exactly one mandatory material-defect fixture for each other category:

- authority continuity;
- identity/qualification bypass;
- privacy/egress;
- malformed records/output handling;
- stale/replay;
- lifecycle/routing/state-machine behavior;
- prompt injection;
- runtime mutation;
- security boundary;
- credential handling;
- automatic execution;
- privilege expansion;
- deployment boundary.

It must also supply at least one sealed known-good control. The minimum suite is
therefore 15 fixtures and 30 trials per actual reviewer identity. Aggregate
metrics can never compensate for one missed mandatory BLOCKING/HIGH defect.

Strong P0 evidence covers the Strong P1 fixtures and risks in this suite. It
does not by itself activate a Strong P1 or Strong P0 registry status; status
inheritance and activation remain separate policy-governed registry
transitions.

## Public R001 binding

The preparation command uses the read-only `PublicR001GitMaterializerV1` and
refuses any provenance mismatch:

- repository: `lxxlx2/local-ai-platform`;
- base: `9aebb5425eb63d82035d6bf1e7e5961b53df93a6`;
- defective head: `a94fd5886a12c744c0e7ccd48cf7ea31124968f2`;
- exact patch SHA-256:
  `129c5c5f5b187453c6f247484fdd1177af38ade7c6fdf6f85f54817d2321c241`;
- paths: `runtime_providers.py`, `workload_execution.py`, and
  `test_workload_execution.py` at their exact repository paths.

Two distinct reviewer-visible materials preserve the same exact diff while
changing only a neutral presentation wrapper. R001 remains public regression
evidence and can never substitute for sealed capability evidence.

The repository-visible aggregate contract is
`benchmarks/review-mesh-g0b-v1/public-fixture-plan-v1.json`. It contains no
sealed fixture ID-to-category map, material, expected result, accepted finding,
evidence path, canary, or label.

## Owner-private sealed source contract

The sealed source is an external JSON file with schema
`OWNER_PRIVATE_SEALED_FIXTURE_SOURCE_V1`. It must remain outside Git, be a
regular non-symlink file with mode `0600`, and have an immediate parent
directory with mode `0700`.

Its top-level fields are:

| Field | Meaning |
|---|---|
| `schema_version` | Exact source schema above |
| `suite_id` | Must match the frozen config |
| `benchmark_version` | Must match the frozen config |
| `custody_version` | Must match the frozen config |
| `variant_seed_commitment_sha256` | Commitment only; never the seed |
| `custodian_identity_record` | Private typed custodian identity record |
| `fixtures` | Private fixture/material/label objects |

The custodian record contains exactly `schema_version`, `custodian_id`, and
`authority_scope`; its schema is `BOOTSTRAP_CUSTODIAN_IDENTITY_V1` and its
scope is `OWNER_PRIVATE_BENCHMARK_CUSTODY`.

Each private fixture contains exactly:

- a blinded `fixture_id` unrelated to its defect category;
- exact allowed paths, privacy class, egress decision, optional privacy canary,
  metamorphic group, and prompt-injection-surface flag;
- at least two curated semantically equivalent variants, each with a distinct
  ID and distinct UTF-8 material;
- private source evidence;
- one exact `OWNER_PRIVATE_FIXTURE_LABEL_V1` label.

The label uses the schema implemented by
`OwnerPrivateFixtureLabelV1`. The materializer rejects label field names,
mandatory-category tokens, and accepted-finding-category tokens found in
reviewer-visible material. This is a deterministic leakage guard, not a claim
that lexical checking alone proves non-contamination; the later custodian
attestation and disclosure-integrity guard remain mandatory.

No sealed source template containing sample defects or labels is committed,
because an ostensibly illustrative template could itself disclose the held-out
mapping.

## Offline preparation command

Repository-visible validation is safe before the final commit:

```bash
python control-plane/scripts/prepare-review-mesh-g0b-bootstrap.py \
  validate-config
```

The full preparation mode is intentionally stricter. Run it only after the
final G0-B commit exists and the worktree is clean, so the proposal binds the
actual repository SHA. The source and output must be explicit Owner-private
paths outside Git:

```bash
python control-plane/scripts/prepare-review-mesh-g0b-bootstrap.py \
  prepare \
  --owner-private-root /ABSOLUTE/OWNER_PRIVATE/review-evidence/g0b-bootstrap-v1/cas \
  --sealed-source /ABSOLUTE/OWNER_PRIVATE/review-evidence/g0b-bootstrap-v1/source/sealed-source-v1.json \
  --epoch-id PROPOSED_G0B_EPOCH_ID \
  --authorized-at 2026-09-02T00:00:00+00:00 \
  --expires-at 2026-09-09T00:00:00+00:00 \
  --allowed-provider EXACT_PROVIDER_PRINCIPAL_A \
  --allowed-provider EXACT_PROVIDER_PRINCIPAL_B \
  --allowed-adapter EXACT_ADAPTER_PRINCIPAL_A \
  --allowed-adapter EXACT_ADAPTER_PRINCIPAL_B
```

The identifiers and timestamps above are placeholders, not authorization. The
Owner must select and approve the exact final values. The command requires at
least two distinct provider principals and two distinct adapter principals and
limits the proposed authorization interval to at most 14 days. It makes only
bounded read-only local Git calls to verify the clean exact commit and
materialize R001. It has no provider client or bootstrap/ledger import path.

## Private outputs and custody separation

The command writes through `ContentAddressedEvidenceStoreV1` only. The explicit
store root and every shard directory are `0700`; every immutable object is
`0600`. Existing objects are re-read and digest-verified. There is no delete,
replace, truncate, or arbitrary destination write API.

The output includes content-addressed objects for:

- public and sealed variant materials and their separate source evidence;
- the reviewer-visible fixture manifest;
- the Owner-private scoring-label manifest;
- separate public and sealed material manifests;
- the variant-seed commitment and custodian identity record;
- the custody manifest;
- a material-pins proposal with no authorization digest;
- an Owner-authorization proposal with no Owner-record digest;
- a bounded preparation index.

The reviewer-visible manifest never includes labels. The stdout summary emits
only digests, CAS references, the exact repository SHA, and explicit false
flags for provider execution, bootstrap transition, and ledger activation. It
does not print fixture material, labels, canaries, or source evidence.

For `BootstrapMaterialPinsV1`, this preparation defines
`public_fixture_manifest_digest` and `sealed_fixture_manifest_digest` as the
separate typed public/sealed material-manifest digests. The
`sealed_label_manifest_digest` is the separately stored Owner-private scoring
label manifest covering the whole suite, including the already-public R001
expectation. The custody manifest additionally binds the complete
reviewer-visible manifest. The material-pins proposal deliberately leaves
`authorization_digest` unset until the exact Owner record exists.

## Required continuation gates

After preparation, the only permitted next action in this slice is to present
the exact proposal digest and fields to the Owner and stop. A later executor
must independently verify, in order:

1. exact Owner authorization and its record digest;
2. material/custody/label digests plus disclosure integrity;
3. two external harness inspections from distinct authenticated provider and
   known foundation lineages, excluding harness/registry contributors;
4. two exact, no-fallback, privacy-permitted qualification identities under
   the closed provider/adapter lists and zero unapproved paid usage;
5. every repeated trial and deterministic zero-violation score;
6. canonical seed registry/package proposal;
7. the second exact Owner seed authorization;
8. atomic ledger genesis and normal-policy activation.

Any expiry, identity/fallback ambiguity, material mismatch, incomplete fixture,
label leakage, provider/adapter mismatch, or failed guard must terminate that
epoch as `BOOTSTRAP_ABORTED`. It must never be repaired by weakening the
configuration or silently reusing the epoch.
