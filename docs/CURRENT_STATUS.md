# Current Project Status

Status type: CANONICAL CROSS-BRANCH SNAPSHOT

Last consolidated: 2026-09-06

This file is the current high-level source of truth for ongoing execution. Historical implementation details remain in Issues, PRs, ADRs and qualification reports; do not reconstruct current state from old chat history when this file and live evidence are available.

## 1. Source-of-truth order

Read in this order:

1. live runtime/resource evidence for current machine state;
2. merged `main` for stable code/docs;
3. this file for current execution state;
4. `docs/MODEL_WORK_CAPABILITY_VALIDATION_V1.md` for direct-work model decisions;
5. `docs/LOCAL_MODEL_INVENTORY.md` for disk/download state;
6. workload qualification evidence and accepted architecture policy;
7. active Issue/PR exact refs;
8. historical docs and chat memory.

## 2. Current execution priority

The project is no longer prioritizing broad infrastructure cleanup or generic benchmark expansion.

Current priority is to prove which already-downloaded local models can directly perform useful work in the existing money-making workflows.

Tracker: Issue #44 `Validate local models against real work tasks`.

Execution order:

1. Whisper livestream/video transcription;
2. Qwen3-TTS Base and VoiceDesign;
3. Qwen3 Embedding + Reranker project retrieval;
4. FLUX workflow image generation, gated by representative memory preflight;
5. Qwen workflow-specific idle/batch roles, with X copy remaining blocked;
6. LongCat video generation, gated by representative memory preflight;
7. RAW Qwen only if a concrete owner-only work role justifies it.

Do not resume Reviewer Mesh, broad provider screening, new model downloads or unrelated benchmark work while this direct-work validation sequence is active.

## 3. Git synchronization state

Completed work has been synchronized and merged rather than left only in local branches/chat:

- PR #42 `Telegram: unify X revenue approval in existing bot` merged to `main` on 2026-09-06.
- PR #43 `Audit live local model inventory` merged to `main` on 2026-09-06.
- The live inventory audit script and generated inventory are now durable in Git.
- Issue #44 tracks direct-work model validation.

Cross-repository X workflow work remains on `lxxlx2/ChatGPT_mission_record` PR #10. That PR is synced remotely but currently not mergeable and must be reconciled before merge. Do not recreate the X workflow from scratch.

## 4. Local model inventory

The 2026-09-06 live disk audit found 10 independent installed model packages with about 168.145 GiB total payload.

Eight queue targets are completion-marker validated. The normal Qwen3.6 and Qwen3.8 packages are also physically present outside that queue proof.

Installed set:

| Model | Primary intended work | Disk payload | Expected runtime memory | Direct-work state |
| --- | --- | ---: | ---: | --- |
| Qwen3.6-35B-A3B-4bit | bounded text / fallback | 19.026 GiB | 28 GiB | X copy `RESOURCE_BLOCKED`; other workflows require separate tests |
| Qwen3.8-27B-8bit | main text / vision | 27.503 GiB | 34 GiB | X copy `RESOURCE_BLOCKED`; workflow-specific vision/text tests pending |
| Qwen3.8 RAW 8-bit | owner-only RAW text | 27.500 GiB | 34 GiB | deferred, no justified work role yet |
| Whisper large-v3 MLX | transcription | 2.872 GiB | 6 GiB | direct test next |
| Qwen3-TTS Base 1.7B | narration | 4.232 GiB | 8 GiB | not tested |
| Qwen3-TTS VoiceDesign 1.7B | designed voice narration | 4.210 GiB | 8 GiB | not tested |
| FLUX.2 klein 4B BF16 | X/novel/sticker images | 22.110 GiB | 30 GiB | not tested; memory preflight first |
| Qwen3 Embedding 8B | retrieval | 14.110 GiB | 20 GiB | not tested |
| Qwen3 Reranker 8B | retrieval reranking | 15.267 GiB | 20 GiB | not tested |
| LongCat Video q8 | generated video | 31.315 GiB | 44 GiB | not tested; memory preflight first |

There are currently no `.incomplete` bytes in the audited model directories. Embedding, Reranker, LongCat and RAW Qwen are now fully downloaded; older docs that described them as partial are stale.

## 5. Workload qualification policy remains authoritative

`docs/qualification/WORKLOAD_QUALIFICATION_POLICY.md` still governs machine-resource claims.

Normal user applications such as Chrome, Codex/ChatGPT, IDEs and Unity must not be closed merely to obtain a model PASS.

A representative resource failure is valid architecture evidence. The system must route around it, defer the task, use another qualified path, or record the capability as unavailable for that deployment mode.

`Downloaded != Registered != Generic Qualified != Direct-Work Approved`.

## 6. Qwen direct-work interpretation

### Qwen3.6

Historical representative workload qualification proved a bounded functional/runtime path.

On 2026-09-06 the model was evaluated specifically as a candidate for X/Twitter content creation under the Owner's normal workstation load. Two production memory preflights denied admission before the model started.

Second measured state:

- expected model memory: 28 GiB;
- required reclaimable threshold under current policy: 23.8 GiB;
- reclaimable memory: 22.45 GiB;
- swap used: 1.4 GiB;
- pressure: NORMAL;
- result: `INSUFFICIENT_RECLAIMABLE_MEMORY`.

The model therefore has no valid X-writing quality verdict. The production decision is still explicit because availability itself fails the intended 7x24 workflow requirement.

Current workflow status: `X_COPY = RESOURCE_BLOCKED / DO_NOT_USE_FOR_X_COPY`.

### Qwen3.8

Qwen3.8 has higher expected memory and prior representative cold-load evidence hit the relative swap-growth safety gate.

Current workflow status: `X_COPY = RESOURCE_BLOCKED / DO_NOT_USE_FOR_X_COPY`.

Qwen may still be useful for separately declared idle/batch novel drafting, bounded assistance or vision work. Those roles require their own direct-work evidence and must not inherit approval from generic MAIN/VISION registry status.

## 7. X revenue workflow current state

The X workflow is already running as a real-source, human-approved vertical slice.

Current architecture:

`Nasdaq/Kraken/Fed/SEC -> deterministic trigger -> deterministic analysis/candidate -> quality/integrity checks -> persisted artifact -> unified @Jersonliu_bot Owner approval -> manual copy/publish`

Important decisions:

- quiet market state returns `NO_ACTION` and sends no Telegram noise;
- local Qwen is not used to generate X copy;
- candidate generation remains deterministic until a local text model passes dedicated `X_COPY` qualification;
- Telegram approval uses the existing bot and a single Telegram update consumer;
- external X publishing remains locked;
- the Owner manually copies approved text into X because paid X API publishing is not currently justified.

The local-ai Telegram integration is merged in PR #42. The X workflow implementation remains synced in `ChatGPT_mission_record` PR #10 and must be reconciled/merged rather than reimplemented.

## 8. Telegram state

The existing `@Jersonliu_bot` is the single approval/control surface for X candidate decisions.

The merged integration:

- discovers pending X artifacts;
- binds approve/reject actions to exact candidate SHA256;
- persists approval state atomically;
- keeps external publishing disabled;
- does not create a second Telegram polling/network client.

## 9. Direct-work model validation rules

Use `docs/MODEL_WORK_CAPABILITY_VALIDATION_V1.md` and Issue #44.

Every model/workflow pair ends as one of:

- `NOT_TESTED`;
- `RESOURCE_BLOCKED`;
- `FUNCTIONAL_FAIL`;
- `QUALITY_FAIL`;
- `LAB_ONLY`;
- `WORKFLOW_PASS`;
- `PRODUCTION_READY`.

A blocked or failed model must be explicitly documented. Do not repeat deterministic failures just to obtain a PASS.

Each valid result must preserve the model/profile, workload class, deployment mode, resource preflight, output evidence, runtime, cleanup and task-specific quality decision.

## 10. Safety and scope constraints

- Port 8199 belongs to another local project and must not be touched by local-model qualification work.
- User work applications have priority over local AI runtimes.
- Only exact-owned model processes may be stopped by qualification tooling.
- No silent paid provider/API fallback.
- External X publishing remains disabled.
- Private media, credentials, runtime state and heavyweight generated outputs stay out of Git; safe metadata/hashes/results may be committed.
- Do not treat a download marker or registry role as proof of work capability.

## 11. Immediate next action

Implement and run the first Issue #44 capability test: `Whisper large-v3 -> LIVESTREAM_STT` using real user-owned video/audio, representative workstation load, resource measurements, transcript artifact and cleanup proof.

After that result is known, update the validation ledger, Issue #44 and this current-status file if routing changes, then merge the evidence before moving to the next model.
