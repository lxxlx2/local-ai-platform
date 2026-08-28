# Current Project Status

Status type: CANONICAL CROSS-BRANCH SNAPSHOT

Last consolidated: 2026-08-29

Stable `main` HEAD at consolidation: `fcbb71d6f10f850ccfbb8dbb28015eae4ceed105`

This file describes the whole project across active/review/reference branches. It does not claim that feature-branch work is merged or deployed.

## 1. Executive summary

Repository governance has been substantially consolidated. PR #20 merged the new README, Governance V2, ADR index, architecture decisions and audited download status into `main`. Historical Issue/branch cleanup then reduced the active GitHub surface without deleting unique implementation/evidence.

Immediate sequence:

1. finish the final legacy-doc branch retirement and review the new evidence-preservation commits on `docs/architecture-ledger`;
2. merge those docs only after independent review and explicit Owner merge authorization;
3. return to Context Architecture V2 Phase 1 and finish static verification/commit/push without rerunning the already-passed full suite unless code changes again;
4. keep model downloads paused until network conditions improve and download-state drift is reconciled;
5. resume independent Codex review when model quota is available;
6. continue the master roadmap.

No current docs/feature status implies merge, deployment or production activation.

## 2. Source-of-truth rules

Read in this order:

1. live runtime evidence for current process/resource facts;
2. merged `main` for stable code;
3. this file for current cross-branch project state;
4. `docs/architecture/INDEX.md` and accepted ADRs;
5. active implementation Issue + exact branch/commit;
6. qualification evidence;
7. historical Issue bodies/docs;
8. chat memory.

See `docs/GOVERNANCE.md`.

## 3. Core provider/runtime state

### Local Qwen3.8 MAIN

- Role: local MAIN/private reasoning and routine production.
- Qualified production context remains 16K until a separate 24K/32K qualification changes policy.
- Local Responses bridge / tool-loop foundation is implemented and previously qualified.
- Qwen is suitable for bounded coding/repair and routine local work; long autonomous engineering/review loops have shown poor convergence and must remain bounded/decomposed.
- A two-file independent review attempt of the Gemini robustness fix timed out at 300 seconds with no verdict after workspace-policy blockers were removed. See `docs/qualification/QWEN_BOUNDED_REVIEW_2026-08-29.md` on the architecture ledger until its docs PR is merged.

### Local Qwen3.6 FAST/FALLBACK

- Downloaded and validated as FAST/FALLBACK.
- Normally stopped/on-demand.

### Gemini

- Advisory reviewer/provider path with official Google API, privacy/egress controls and structured review is implemented on feature branches.
- Large review bundles for routing, exact cancellation and durable-state/security returned PASS with zero findings after sanitization-aware review packaging.
- Gemini provider robustness fix is committed/pushed on `fix/gemini-large-review-v01` at `4d229c08adf1ed0716b3922322dca258b625b569`.
- Focused/failover/full tests passed for that fix, plus real Gemini API smoke.
- Final independent non-Gemini review of that fix is still pending. The bounded Qwen review produced no verdict within 300 seconds.
- Gemini supplementary execution/P3 structured patch path is not yet fully implemented or activated.

### Codex

- Interactive provider priority is Codex -> Qwen -> Gemini supplementary for Owner-present engineering, while unattended/routine production remains local-first.
- Codex model quota is currently unavailable, so independent Codex review work is paused.
- Codex CLI may still be used as a local harness only when configured to the local Qwen provider and route attribution proves no OpenAI model usage.

## 4. Repository governance state

Governance baseline merged through PR #20 at `fcbb71d6f10f850ccfbb8dbb28015eae4ceed105` after Gemini independent review PASS with zero findings.

Issue cleanup completed:

- closed/superseded/completed: #1, #2, #3, #4, #5, #6;
- active roadmap/execution issues: #13, #14, #15, #16, #17, #18, #19.

Remote branch cleanup completed in two stages:

- 10 branches with no remaining execution value were deleted after containment review;
- three historical branches with unique commits were first preserved through annotated archive tags, verified against exact target SHAs, then deleted.

Archive tags:

- `archive/media-v02-quota-cutoff-20260828` -> `59a5cf22e774f26c0b103eeddc77680f8708d2b7`;
- `archive/local-producer-v01` -> `c7887cb9c40675e113a1154f7f5528d8c6cfc721`;
- `archive/supervisor-start-identity-v01` -> `c09fe9c80bd5c01fa9005c63a4c7cea7514c3aea`.

`docs/architecture-ledger` was fast-forwarded to the merged governance baseline before new evidence-preservation commits were added.

The remaining legacy docs branch `docs/context-architecture-v2` was audited against frozen failover base `d218f82d39b06b41aac27aaf867ad54a66443563`. Its 11 unique commits are documentation-only. Long-term-value design/qualification records have now been copied to `docs/architecture-ledger`; the old Architecture Change Protocol is intentionally not restored because `main` contains the newer Governance V2 protocol. Preserve the exact legacy head with an archive tag before deleting that branch.

## 5. Important surviving branches

| Branch | State | Purpose / current status |
|---|---|---|
| `main` | STABLE | Governance consolidation merged at `fcbb71d...`; stable code baseline still does not include all latest qualified feature work. |
| `docs/architecture-ledger` | ACTIVE | Single long-lived docs/ADR/qualification ledger. Currently contains post-PR#20 evidence-preservation commits awaiting docs review/merge. |
| `docs/context-architecture-v2` | RETIRING | Legacy aggregate docs branch at `1806d755...`. Its 11 unique commits beyond `d218f82...` are docs-only and their durable value has been migrated. Archive exact head before deletion. |
| `feat/context-budget-manager-v01` | ACTIVE | Context V2 Phase 1. Local worktree has uncommitted implementation; compatibility test PASS, focused 57 PASS, full suite 686 PASS, diff check PASS. Post-test verification failed only because PYTHONPATH was missing after returning to repo root. |
| `feat/codex-desktop-auto-failover-v01` | FROZEN_REVIEW | Qualified Codex -> Qwen same-job failover baseline at `d218f82d39b06b41aac27aaf867ad54a66443563`; independent Codex review paused by quota. Do not mutate baseline. |
| `fix/gemini-large-review-v01` | FROZEN_REVIEW | Gemini large structured review robustness fix at `4d229c08...`; tests and real API smoke passed; non-Gemini independent review pending. |
| `feat/local-qwen-generic-project-v03` | REFERENCE / FUTURE WORK | Generic-project implementation history for Issue #13; future work should align with shared provider/router architecture rather than assume the old Qwen-only path is canonical. |
| `feat/local-qwen-owner-raw-v04` | REFERENCE / PLAN SOURCE | Earlier RAW/provider/product planning. RAW download target must be reconciled before resume. |
| `feat/private-learning-engine-v01` | REFERENCE / FUTURE WORK | Training/learning-engine implementation history; not a currently active production path. |

## 6. Context Architecture V2

Architecture: ACCEPTED.

Tracker: Issue #19.

Phase 1 goal: add a host-side ContextBudgetManager so Chat/Memory can use more of the already-qualified context envelope without changing the Qwen production ceiling.

Current local implementation evidence on `feat/context-budget-manager-v01`:

- prior compatibility failure reproduced and fixed;
- exact previous failure test: 1 PASS;
- focused Context/Chat/Gateway tests: 57 PASS;
- full control-plane suite: 686 PASS, 2 pre-existing Pytest collection warnings;
- `git diff --check`: PASS;
- runtime/model registry intentionally unchanged;
- final standalone verification needs rerun with correct `PYTHONPATH`;
- implementation not yet committed/pushed;
- independent review not yet completed;
- no production activation.

Later phases: Qwen 24K/32K qualification, Gemini token-aware context tiers, then shared context/retrieval contracts for coding/X/novels/commerce/media.

The full approved design is preserved as `docs/CONTEXT_ARCHITECTURE_V2.md` on the architecture ledger in addition to the concise ADR-0003.

## 7. Model download state

Downloads are intentionally PAUSED because network conditions are currently poor. No download-related process was present during the 2026-08-29 audit. Quarantine count was zero. About 586 GiB disk space was free.

Physically complete and marker/snapshot validated:

- Whisper Large V3: 2.872 GiB.
- Qwen3-TTS Base: 4.232 GiB.
- Qwen3-TTS VoiceDesign: 4.210 GiB.
- FLUX.2 klein 4B bf16: 22.110 GiB.

These still need role-specific qualification before production use.

Incomplete/paused, using completed payload percentage rather than misleading cache-inflated percentage:

- Qwen3 Embedding 8B: 9.469 / 14.110 GiB payload, about 67.1%; 10.723 GiB partial cache.
- Qwen3 Reranker 8B: 3.969 / 15.267 GiB payload, about 26.0%; 23.633 GiB partial cache.
- LongCat Video q8: 0.625 / 31.315 GiB, about 2.0%.
- configured RAW MLX target: 0 / 27.500 GiB in its configured directory.

Known download-state defects/drift:

1. the old status command reports Embedding/Reranker as 99.9% because duplicate/resumable `.incomplete` fragments can exceed expected size; canonical human progress must use payload percentage plus separate partial-cache bytes;
2. stored manager state still contains stale PID/identity metadata even though no manager process exists;
3. RAW queue config currently targets `raw-qwen38-27b-8bit` / `orcarouter/Qwen3.8-27B-Uncensored-MLX`, while historical runtime state tracks `raw-qwen38-27b-q6k` / `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`; partial data exists in the historical target directory;
4. unrelated/older `.incomplete` files also exist under the main Qwen3.8 model directory and must not be deleted automatically.

Do not resume RAW until target/revision/runtime/local-dir state is explicitly reconciled.

See `docs/DOWNLOAD_STATUS.md`.

## 8. Roadmap status

Master infrastructure roadmap: Issue #14.

Product end-state: Issue #15.

Active execution/workflow issues:

- #13 Generic project adapter;
- #16 Novel workflow engine;
- #17 Revenue workflows;
- #18 Interactive provider priority / P3 implementation;
- #19 Context Architecture V2.

Major remaining work includes:

- Context V2 remaining phases;
- Gemini supplementary execution/P3;
- generic project adapter completion on shared provider/router abstractions;
- download cleanup/resume and model qualification;
- Embedding/Reranker/RAG;
- FLUX/image production and Code Canvas/Qwen-Image evaluation;
- Whisper/TTS providers;
- X/Twitter production workflow;
- commerce research agent;
- novel project adapters/orchestration;
- video/livestream pipeline;
- Telegram unified task/review control surface;
- training/adaptation pipeline;
- eventual external multi-user isolation;
- final integrated E2E/independent review/merge gate.

Closed Issues #1-#6 are historical/superseded/completed records and are no longer active sources of execution state.

## 9. Branch protection constraint

`main` currently reports `protected=false`.

The GitHub repository is private. Repository rulesets returned a plan-level 403 indicating that this feature requires GitHub Pro or a public repository. Classic branch-protection access through the current integration also returned 403. Do not claim `main` is protected until this is changed and re-verified through an eligible GitHub plan/UI/API.

Until then, protection is procedural:

- no direct feature merge without the documented review/Owner gate;
- no force push to `main`;
- no deleting `main`;
- keep implementation on feature branches/worktrees;
- verify exact refs before push/merge.

## 10. Hard safety constraints

- Local-first routine production.
- Host/Supervisor owns permissions.
- No arbitrary PID kill or fuzzy process ownership.
- Producer cannot satisfy its own required independent review.
- Gemini/cloud egress remains privacy gated; no silent paid billing.
- Models, secrets, runtime state, raw credentials, private caches, `.incomplete` download data and sensitive DBs remain outside Git.
- Downloaded != qualified != deployed.
- No merge/deploy/external publish/irreversible action is implied by a feature/docs commit.

## 11. Next action after governance

1. archive tag and delete `docs/context-architecture-v2` after verifying its exact head;
2. independently review the docs-only delta currently on `docs/architecture-ledger` and merge it to `main` only after explicit Owner authorization;
3. return to `feat/context-budget-manager-v01` and rerun only the remaining post-test verification with correct `PYTHONPATH`;
4. do not repeat the already-passed 686-test full suite unless the implementation changes again;
5. inspect final Context V2 Phase 1 diff, commit/push, update Issue #19, then proceed to independent review.
