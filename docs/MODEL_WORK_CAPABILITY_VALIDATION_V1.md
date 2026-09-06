# Local Model Direct-Work Capability Validation V1

Status: OWNER-REQUESTED / EXECUTION CURRENT

Tracker: Issue #44

This document defines how the local models already present on the workstation are validated against the real jobs they were intended to support. It adds a workflow-capability layer on top of `docs/qualification/WORKLOAD_QUALIFICATION_POLICY.md`.

## 1. Why this exists

The repository previously tracked model registration, downloads, generic runtime qualification and provider wiring. Those facts do not prove that a model can perform a useful production task.

The platform must distinguish four questions:

1. Is the model physically present and complete?
2. Can the runtime safely load and coexist with the Owner's normal workload?
3. Can the model complete a representative task for a specific workflow?
4. Is the output good enough and reliable enough to be used in that workflow?

A positive answer to an earlier question does not imply a positive answer to a later one.

## 2. Source-of-truth order

For local-model work capability, read evidence in this order:

1. live runtime and resource evidence;
2. workflow-specific direct-work evidence produced under this plan;
3. `docs/LOCAL_MODEL_INVENTORY.md` for physical/download state;
4. `config/qualification-evidence-v0.1.json` for generic workload qualification;
5. `config/model-registry-v0.1.json` for registered roles and eligibility;
6. architecture/docs/history;
7. chat memory.

`REGISTERED`, `VALIDATED` or `QUALIFIED` in the generic registry must never be interpreted as approval for every workflow.

## 3. Status vocabulary

Every model/workflow pair must end with one of these states:

- `NOT_TESTED`: no valid direct-work evidence yet.
- `RESOURCE_BLOCKED`: the intended workload cannot safely admit or sustain the model.
- `FUNCTIONAL_FAIL`: the runtime starts but the task cannot be completed correctly.
- `QUALITY_FAIL`: the task completes but the result is not useful enough for the intended workflow.
- `LAB_ONLY`: useful behavior exists only under deliberately reduced workload and is not valid for normal production.
- `WORKFLOW_PASS`: representative task and quality gates pass, but production integration may still be pending.
- `PRODUCTION_READY`: representative task, resource, cleanup and integration gates pass for the declared deployment mode.

A durable `DO_NOT_USE` decision may be attached to any blocked/failed state for a named workflow.

## 4. Current installed-model baseline

The 2026-09-06 live disk audit found 10 independent local model packages, about 168.145 GiB total payload. Eight queue targets are marker-validated complete; Qwen3.6 and the normal Qwen3.8 package are also physically present outside the download queue proof.

| Profile / model | Intended role | Expected memory | Current direct-work status |
| --- | --- | ---: | --- |
| `local-qwen36` / Qwen3.6-35B-A3B-4bit | FAST / FALLBACK / bounded text | 28 GiB | X copy: `RESOURCE_BLOCKED`, `DO_NOT_USE_FOR_X_COPY`; other workflow roles require separate tests |
| `local-qwen38` / Qwen3.8-27B-8bit | MAIN / VISION / DEEP | 34 GiB | X copy: `RESOURCE_BLOCKED`, `DO_NOT_USE_FOR_X_COPY`; vision/general roles need workflow-specific evidence |
| `owner-qwen38-raw` / Qwen3.8 RAW 8-bit | owner-only RAW text | 34 GiB | `NOT_TESTED`; no production role justified yet |
| `whisper-large-v3` | livestream/video transcription | 6 GiB | `NOT_TESTED`; first priority |
| `qwen3-tts-base` | narration TTS | 8 GiB | `NOT_TESTED` |
| `qwen3-tts-design` | designed voice TTS | 8 GiB | `NOT_TESTED` |
| `flux2-klein` | X visuals / novel concept art / sticker images | 30 GiB | `NOT_TESTED`; representative memory preflight first |
| `qwen3-embedding` | retrieval embedding | 20 GiB | `NOT_TESTED` |
| `qwen3-reranker` | retrieval reranking | 20 GiB | `NOT_TESTED` |
| `longcat-video` | generated video | 44 GiB | `NOT_TESTED`; representative memory preflight first |

`wan21-video` remains a registered candidate without a finalized exact repository and is not part of the installed-model validation set.

## 5. X/Twitter direct-work decision

The X revenue design remains:

`real sources -> deterministic trigger/detection -> deterministic analysis/candidate -> quality checks -> unified Telegram Owner approval -> manual copy/publish`

External X publishing stays locked.

### Qwen3.6

On 2026-09-06, two representative preflight attempts were performed while normal work applications remained open.

Observed reclaimable memory stayed below the production admission threshold:

- first attempt: about 21.67 GiB available/reclaimable range, admission denied;
- second attempt: 22.45 GiB reclaimable;
- Qwen3.6 expected memory: 28 GiB;
- required reclaimable threshold under current policy: 23.8 GiB;
- result: `INSUFFICIENT_RECLAIMABLE_MEMORY` before model start.

Therefore Qwen3.6 has no valid X-writing quality verdict because the model never started. The production conclusion is still explicit: it cannot be relied on for 7x24 X copy generation under the Owner's representative workstation load.

Status: `X_COPY = RESOURCE_BLOCKED / DO_NOT_USE_FOR_X_COPY`.

### Qwen3.8

Qwen3.8 has higher expected memory, and prior representative cold-load evidence already hit the relative swap-growth safety gate. It is not approved as an X copy generator on this workstation.

Status: `X_COPY = RESOURCE_BLOCKED / DO_NOT_USE_FOR_X_COPY`.

### Promotion rule for future text models

A local text model may enter X candidate generation only after a dedicated `X_COPY` qualification proves all of the following under `REPRESENTATIVE_WORKLOAD`:

- safe cold-load or separately qualified preloaded-daemon mode;
- 10 real saved X market artifacts processed without factual invention;
- zero unsupported numbers/events/causes;
- zero posts above 280 characters;
- no direct buy/sell instruction or return promise;
- at least 7/10 outputs judged materially more useful than the deterministic baseline;
- bounded latency compatible with the scheduled workflow;
- cleanup/resource gates pass;
- human Telegram approval remains mandatory.

Until then the deterministic X candidate path is canonical.

## 6. Direct-work test suites

### 6.1 Whisper large-v3: `LIVESTREAM_STT`

Use real user-owned livestream/video clips rather than synthetic tones.

Minimum test set:

- one Chinese speech clip;
- one English speech clip;
- one noisy/mixed real clip from the intended clipping workflow when available.

Required evidence:

- timestamped transcript artifact;
- runtime and real-time factor;
- memory/swap/pressure before and during run;
- cleanup proof;
- spot-check of names, numbers and sentence meaning.

Pass condition:

- all clips complete without crash;
- transcript is usable for downstream clip selection without wholesale manual retranscription;
- timestamps are usable for seeking/cutting;
- no host safety gate is violated.

### 6.2 Qwen3-TTS Base: `NARRATION_TTS`

Generate short Chinese and English narration scripts representative of short-video output.

Pass condition:

- valid playable audio on every sample;
- intelligible speech with no truncation or severe artifacts;
- acceptable pacing;
- bounded generation time;
- resource and cleanup gates pass.

### 6.3 Qwen3-TTS VoiceDesign: `VOICE_DESIGN_TTS`

Use at least three style instructions that are materially different.

Pass condition:

- output remains intelligible;
- requested style differences are audibly present;
- no clipping/truncation;
- resource and cleanup gates pass.

This test does not authorize impersonation or voice cloning.

### 6.4 Qwen3 Embedding + Reranker: `PROJECT_RETRIEVAL`

Use real repository/project documents and questions whose target documents are known in advance.

Minimum test set: 10 queries across current projects.

Embedding pass target:

- expected target appears in top-5 for at least 9/10 queries.

Reranker pass target:

- expected target ranks top-1 for at least 8/10 queries when given a reasonable candidate set.

Both models must also pass representative resource and cleanup gates.

### 6.5 FLUX.2 klein: `WORKFLOW_IMAGE_GENERATION`

Test only after representative memory preflight passes.

Use three real task classes:

1. X/social visual without text-heavy layout requirements;
2. novel character/environment concept image;
3. sticker/expression-style image.

Required evidence:

- generated files and generation time;
- requested dimensions;
- resource peak and cleanup;
- Owner usefulness rating for each task.

Pass target:

- all three task classes produce valid images;
- at least two of three are judged directly usable or usable with light editing;
- no host safety gate is violated.

If preflight blocks under representative workload, record `RESOURCE_BLOCKED`; do not close Chrome/Codex to force a pass.

### 6.6 Qwen3.6 / Qwen3.8 non-X text roles

X copy is already blocked for normal 7x24 use. Other text roles may still be valuable under a different deployment mode.

Candidate roles:

- `NOVEL_DRAFT_IDLE_BATCH`;
- bounded summarization from already-provided facts;
- bounded coding/repair assistance;
- Qwen3.8 vision tasks on real project images.

Each role needs its own test fixture and deployment declaration. A LAB or idle-batch pass must not be relabeled as always-on production capability.

### 6.7 LongCat Video: `VIDEO_GENERATION`

Run representative memory preflight before any model load.

Initial functional target is deliberately small: a short low-duration clip sufficient to prove the end-to-end generation path and prompt adherence.

Pass condition:

- valid playable video;
- prompt/scene relation is recognizable;
- bounded runtime;
- no host safety violation;
- cleanup succeeds.

A resource preflight denial is a valid final result for the current workstation/deployment mode.

### 6.8 RAW Qwen3.8

Do not spend workstation resources validating RAW merely because it is downloaded. A concrete owner-only workflow must justify the test first.

Current state: `NOT_TESTED / DEFERRED_NO_WORK_ROLE`.

## 7. Execution order

The order is chosen by direct utility to the existing money-making workflows and probability of fitting the workstation:

1. Whisper `LIVESTREAM_STT`.
2. Qwen3-TTS Base and VoiceDesign.
3. Embedding + Reranker retrieval pair.
4. FLUX image generation, beginning with memory preflight.
5. Qwen text roles that are useful in idle/batch operation; X copy remains blocked.
6. LongCat video, beginning with memory preflight.
7. RAW Qwen only if a concrete owner-only role appears.

Do not restart broad model-screening, Reviewer Mesh, unrelated benchmark work, or new downloads during this sequence.

## 8. Evidence contract

Every test result must record:

- `profile_id` and exact model ID/path;
- workflow capability ID;
- workload class;
- deployment mode;
- exact start/end timestamps;
- preflight snapshot;
- model start result;
- task input reference, without committing private raw media when inappropriate;
- output artifact reference/hash;
- runtime/latency;
- resource samples and swap delta;
- cleanup state;
- automatic quality checks where possible;
- Owner/manual quality decision where required;
- final status and reason.

Results belong in durable Git-tracked qualification summaries. Private media, secrets and heavyweight outputs remain outside Git; only safe metadata/hashes/references are committed.

## 9. Stop rules

Stop a model/workflow test immediately when:

- representative memory preflight denies admission;
- fixed ports or process ownership are ambiguous;
- the test would require killing/closing normal user applications;
- memory pressure becomes critical or swap policy is exceeded;
- the model cannot produce the required output type;
- the same deterministic failure has already been reproduced and no code/config changed.

Do not repeat failures merely to obtain a PASS.

## 10. Documentation update rule

After every direct-work test:

1. update the per-workflow result in this document or its generated result ledger;
2. update Issue #44;
3. update `docs/CURRENT_STATUS.md` when the decision changes production routing;
4. update the relevant workflow repository when a model is approved or rejected for that workflow;
5. merge completed evidence/documentation so another chat/session sees the same state from Git rather than reconstructing it from conversation history.
