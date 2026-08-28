# Current Project Status

Status type: CANONICAL CROSS-BRANCH SNAPSHOT

Last consolidated: 2026-08-29

Stable `main` HEAD at consolidation: `1311f7d05309d75af61bbd8990d4a0641ba70d56`

This file describes the whole project across active/review/reference branches. It does not claim that feature-branch work is merged or deployed.

## 1. Executive summary

The platform foundation is substantially implemented, but the repository history has outgrown the old main-branch status document. Current work is therefore split between stable `main`, qualified/review-pending feature branches, and one active Context Architecture V2 implementation branch.

Immediate sequence:

1. finish repository governance consolidation on `docs/architecture-ledger`;
2. return to Context Architecture V2 Phase 1 and finish static verification/commit/push without rerunning the already-passed full suite;
3. keep model downloads paused until network conditions improve and download-state drift is reconciled;
4. resume independent Codex review when model quota is available;
5. then continue the master roadmap.

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
- Qwen is suitable for bounded coding/repair and routine local work; long autonomous engineering loops have shown poor convergence and must remain bounded/decomposed.

### Local Qwen3.6 FAST/FALLBACK

- Downloaded and validated as FAST/FALLBACK.
- Normally stopped/on-demand.

### Gemini

- Advisory reviewer/provider path with official Google API, privacy/egress controls and structured review is implemented on feature branches.
- Large review bundles for routing, exact cancellation and durable-state/security returned PASS with zero findings after sanitization-aware review packaging.
- Gemini provider robustness fix is committed/pushed on `fix/gemini-large-review-v01` at `4d229c08adf1ed0716b3922322dca258b625b569`.
- Focused/failover/full tests passed for that fix, plus real Gemini API smoke.
- Final independent non-Gemini review of that fix is still pending. A bounded Qwen review timed out at 300 seconds and produced no verdict.
- Gemini supplementary execution/P3 structured patch path is not yet fully implemented or activated.

### Codex

- Interactive provider priority is Codex -> Qwen -> Gemini supplementary for Owner-present engineering, while unattended/routine production remains local-first.
- Codex model quota is currently unavailable, so independent Codex review work is paused.
- Codex CLI may still be used as a local harness only when configured to the local Qwen provider and route attribution proves no OpenAI model usage.

## 4. Important branches

| Branch | State | Purpose / current status |
|---|---|---|
| `main` | STABLE | Stable baseline `1311f7d...`; does not contain all latest qualified features. |
| `docs/architecture-ledger` | ACTIVE | Single governance/ADR/current-status branch. Replaces the pattern of one docs branch per decision. |
| `feat/context-budget-manager-v01` | ACTIVE | Context V2 Phase 1. Local worktree currently has uncommitted implementation; compatibility test PASS, focused 57 PASS, full suite 686 PASS, diff check PASS. Post-test verification command failed only because PYTHONPATH was missing after returning to repo root. |
| `feat/codex-desktop-auto-failover-v01` | FROZEN_REVIEW | Qualified Codex -> Qwen same-job failover baseline at `d218f82d39b06b41aac27aaf867ad54a66443563`; independent Codex review paused by quota. Do not mutate baseline. |
| `fix/gemini-large-review-v01` | FROZEN_REVIEW | Gemini large structured review robustness fix at `4d229c08...`; tests and real API smoke passed; non-Gemini independent review pending. |
| `feat/production-capabilities-v01` | REFERENCE | Contains download manager and earlier production-capability work; current download audit should be read from `docs/DOWNLOAD_STATUS.md`, not old percentages in historical docs. |
| `feat/local-qwen-owner-raw-v04` | REFERENCE / PLAN SOURCE | Contains approved local-first/product/model plan and earlier RAW target planning. RAW target needs reconciliation before resume. |
| older per-decision docs branches | REFERENCE | Preserve history until ADR migration/merge makes them safely retireable. Do not add new architecture work there. |

Branch cleanup will happen only after unique decisions/evidence are durable elsewhere.

## 5. Context Architecture V2

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

## 6. Model download state

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

## 7. Roadmap status

Master infrastructure roadmap: Issue #14.

Product end-state: Issue #15.

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

Historical Issue bodies such as Issue #1 may still describe Gemini as roadmap-only. Those bodies are historical and no longer represent implementation state; this file and ADR/index records take precedence until Issues are individually normalized.

## 8. Repository governance transition

Repository governance is being normalized on `docs/architecture-ledger`:

- one README entrypoint;
- one canonical current-status snapshot;
- one ADR index;
- ADR files for approved architecture decisions;
- Issues for execution rather than architecture canon;
- feature branches for implementation;
- qualification records for evidence;
- one long-lived docs ledger only when an implementation branch is frozen.

After this governance branch is reviewed and explicitly merged, old docs branches can be classified/retired gradually instead of being cleaned up blindly.

## 9. Hard safety constraints

- Local-first routine production.
- Host/Supervisor owns permissions.
- No arbitrary PID kill or fuzzy process ownership.
- Producer cannot satisfy its own required independent review.
- Gemini/cloud egress remains privacy gated; no silent paid billing.
- Models, secrets, runtime state, raw credentials, private caches, `.incomplete` download data and sensitive DBs remain outside Git.
- Downloaded != qualified != deployed.
- No merge/deploy/external publish/irreversible action is implied by a feature/docs commit.

## 10. Next action after governance

Return to `feat/context-budget-manager-v01` and rerun only the remaining post-test verification with correct `PYTHONPATH`; do not repeat the already-passed 686-test full suite unless the code changes again. Then inspect final diff, commit/push Phase 1, update Issue #19, and proceed to review.
