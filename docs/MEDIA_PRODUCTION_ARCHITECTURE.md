# Local media production architecture

Status: architecture baseline plus qualification evidence. Presentation narration/video V0.1 and Media Product Workflow V0.2 are implemented on `feat/local-qwen-owner-raw-v04` and locally qualified. Generative video and trainable persona adapters remain staged work.

## Goal

The media plane turns an Owner task, uploaded materials, approved links, or an Owner-provided script into a reproducible local media product without making Codex or any cloud model a runtime dependency.

```text
Owner task / uploads / links / script / PPTX
  -> deterministic intake + private job workspace
  -> requirement/evidence extraction
  -> optional local Qwen3.8 production brief/script/scene generation
  -> durable final script / scene artifacts
  -> language + persona + media routing
  -> audio / presentation / image / video adapters
  -> local render/composition
  -> Owner preview/review
  -> exact-output approval
  -> deterministic product publishing
  -> verified local cleanup
```

The final script and production plan are first-class durable artifacts. A model may propose or generate them, but downstream synthesis/rendering consumes persisted artifacts rather than hidden conversational state.

## MediaJob intake modes

A video/media task may begin from four simple Owner-facing sources:

1. `uploads`: one or more validated files supplied by the Owner;
2. `links`: one or more Owner-supplied public URLs containing requirements/reference material;
3. `uploads+links`: both local uploads and public evidence/requirements;
4. `brief`: a direct natural-language task description.

These are intake methods, not different execution engines. They converge into the same durable MediaJob.

Supported script origins remain:

- `owner-script`: Owner supplies final narration directly;
- `presentation-notes`: PPT speaker notes are used;
- `local-qwen-script`: local Qwen creates a script from the requirement/brief;
- `hybrid`: Owner material is authoritative where present and local Qwen fills explicitly missing portions.

First-class CLI/API interfaces should expose direct script/brief/link ingestion rather than requiring manual `narration.json` edits.

Target interfaces:

```text
media prepare --task <name> --script-file <path>
media prepare --task <name> --brief-file <path> --script-generator local-qwen
media prepare --task <name> --url <url>
presentation build --input <pptx> --script-file <path>
```

## Requirement intake from links

Owner-supplied URLs are retrieved only through the bounded Search/Browser layer. Retrieved pages, linked documents, OCR/transcript text and model summaries remain `UNTRUSTED DATA`.

Canonical flow:

```text
Owner URL(s)
  -> URL validation / bounded fetch
  -> source URL + retrieval time + content hash + provenance
  -> requirement extraction
  -> optional follow of relevant public official references
  -> durable source_evidence.json
  -> durable requirements.json / requirements.md
  -> production planning
```

Requirement extraction may identify:

- requested deliverable/prompt;
- number of videos or outputs;
- duration/size/format constraints;
- mandatory questions/content;
- required language;
- submission instructions;
- deadlines;
- official evaluation criteria;
- official reference links;
- explicit prohibitions or constraints.

Fetched text never authorizes shell, downloads, installs, filesystem writes, Git actions, credential access, model routing changes or privilege expansion. If a page is inaccessible because of login/CAPTCHA/network failure, the workflow reports the missing source and asks for an upload/retry rather than inventing content.

## Durable production artifacts

After intake, local Qwen may convert the validated evidence/requirements into a set of durable production artifacts:

```text
requirements.json / requirements.md
production_brief.md
script.txt
scene_plan.json
prompt_pack.json
source_evidence.json
```

The exact subset depends on the media type. These files are editable, hashed and independently reviewable. They make media production portable across model upgrades and prevent hidden chat state from becoming the only source of truth.

If the task requires a real Owner-specific fact that cannot be supported by provided materials or approved project context, the MediaJob enters `MISSING_OWNER_FACT`. Qwen must ask the smallest necessary question and must not fabricate personal experience, credentials, events or other real-world facts.

## Execution style

Two normal Owner modes are supported:

```text
AUTO_COMPLETE
SCRIPT_REVIEW_FIRST
```

`AUTO_COMPLETE` allows requirement analysis -> script/scene plan -> media generation -> final preview without an intermediate Owner script gate.

`SCRIPT_REVIEW_FIRST` stops at `SCRIPT_READY` and requires Owner continue/revise before expensive synthesis.

Neither mode bypasses the final exact-output review/publish gate.

## Current qualified presentation path

```text
PPTX
  -> safe parse + render
  -> final per-slide narration
  -> deterministic language detection
  -> qualified persistent VoiceProfile
  -> local Qwen3-TTS Base
  -> per-slide WAV
  -> actual-duration timeline
  -> FFmpeg H.264/AAC MP4
```

This path has completed real local E2E runs with both automatic narration generation and Owner-provided English scripts. See `PRESENTATION_VOICE_ARCHITECTURE.md`.

## Generic script-to-audio path

The same TTS subsystem should work without a PPTX:

```text
final script
  -> language detection
  -> VoiceProfile
  -> safe sentence/paragraph chunking
  -> local Qwen3-TTS Base
  -> WAV/FLAC/MP3 output
  -> transcript/timing manifest
```

Normal synthesis reuses persistent qualified VoiceProfiles and never recreates VoiceDesign references for each job.

## Generic task/script-to-presentation/video path

For presentation-like jobs, a task/requirement can be turned into a structured presentation plan even when the Owner does not provide a PPTX:

```text
requirements / brief
  -> local Qwen3.8
  -> production_brief.md
  -> script.txt
  -> slide/scene plan
  -> deterministic template-based slide/render adapter
  -> TTS
  -> timeline
  -> MP4
```

This provides a useful no-generative-video fallback: even before FLUX/LongCat qualification, the system can create a basic presentation-style video from text/requirements using deterministic local rendering.

When local image/video providers become qualified, the same `scene_plan.json` and `prompt_pack.json` can route to richer image/video generation without changing the MediaJob intake contract.

## Telegram video intake

Telegram is the preferred simple Owner entry point. It must not require command syntax, profile ids, filesystem paths, JSON or Git knowledge.

Canonical wizard:

```text
媒体
  -> 视频
  -> 新建视频
  -> 名称
  -> 材料从哪里来
       |- 上传材料
       |- 发送链接
       |- 上传材料 + 链接
       `- 直接描述任务
  -> intake / requirements
  -> 执行方式
       |- 自动完成
       `- 先看文稿
  -> 语言
  -> 声音/Persona
  -> 完成后处理
  -> 确认
  -> 生成
  -> 预览
  -> Owner 决策
```

Normal language choices are `自动 / 中文 / English`. Normal voice choices expose friendly labels for qualified profiles only. Advanced model/profile names remain hidden. See `BOT_UX.md`.

## PersonaProfile

A reusable human-like identity is represented as a private `PersonaProfile`:

```text
PersonaProfile
  |- VoiceIdentityProfile
  |- VisualIdentityProfile
  |- optional Motion/VideoIdentityProfile
  |- provenance + consent metadata
  |- qualification state
  |- version/revision bindings
```

Each sub-profile can evolve from reference-based inference to a trained adapter/model without changing callers.

### VoiceIdentityProfile

Current capabilities:

- qualified default Chinese/English persistent voices;
- Qwen3-TTS VoiceDesign reference generation;
- Qwen3-TTS Base reference-voice cloning;
- explicit job-scoped Owner voice reference support.

Future persistent custom-person flow:

```text
Owner-provided consented voice samples
  -> private dataset intake
  -> quality/transcript checks
  -> zero-shot reference baseline
  -> optional speaker fine-tune/adapter training
  -> held-out evaluation
  -> Owner approval
  -> qualified VoiceIdentityProfile revision
```

A trained speaker backend is a new profile revision. Existing jobs remain bound to their recorded revision/hash.

### VisualIdentityProfile

Future flow:

```text
Owner-provided consented image/video material
  -> private dataset intake
  -> quality/duplicate checks
  -> captions/metadata
  -> baseline reference generation
  -> optional LoRA/adapter fine-tuning
  -> identity-consistency evaluation
  -> Owner approval
  -> qualified VisualIdentityProfile revision
```

Adapter-based training is preferred over modifying a shared base model for each person.

## Generative video

The qualified presentation-video pipeline is deterministic composition, distinct from generative video.

Future generative path:

```text
script / scene plan
  -> local Qwen3.8 scene decomposition
  -> optional VisualIdentityProfile
  -> image/keyframe generation
  -> qualified local video backend
  -> optional voice track
  -> optional lip-sync / talking-avatar adapter
  -> FFmpeg composition
  -> MP4
```

A generative video backend is available only after target-Mac qualification. Registered/downloaded/partial alone is not READY.

## Training plane

Training is isolated from normal inference jobs:

```text
private training dataset
  -> dataset manifest + provenance/consent
  -> immutable train/validation split
  -> training job
  -> checkpoint/adapter artifacts
  -> deterministic evaluation
  -> Owner review
  -> profile qualification
```

Training records dataset hashes, subject/profile id, provenance/consent, base model revision, training code revision, hyperparameters/seed where supported, checkpoint hashes, evaluation outputs and Owner qualification decision.

Suggested private roots:

```text
/Users/jerson/AI/private/personas/<persona-id>/source/
/Users/jerson/AI/private/personas/<persona-id>/datasets/
/Users/jerson/AI/private/personas/<persona-id>/training/
/Users/jerson/AI/runtime/persona-profiles/<persona-id>/
```

Raw voice, face, video and training-source material never enters the public product repository automatically.

## Canonical product repository contract

All approved video products use:

```text
lxxlx2/ai_video_product
```

Canonical V0.2 task layout:

```text
<task-slug>/
  README.md
  source/
    presentation.pptx      optional
    script.txt             optional owner source
    links.json             optional
  generated/
    requirements.md        optional
    production_brief.md    optional
    script.txt             optional generated/final script copy
    scene_plan.json        optional
    prompt_pack.json       optional
  output/
    final.mp4
  metadata/
    manifest.json
    provenance.json
    narration.json         optional
    timeline.json          optional
    publish.json
```

Rules:

- `output/final.mp4` is the stable approved video path;
- revisions of the same task update the same path; Git history preserves prior versions;
- do not create ad-hoc names such as `final-v2-final2.mp4`;
- source/generated files are included only when approved/safe for the public product repository;
- private persona/training assets, credentials, private checkpoints and private intermediates never enter this repository automatically;
- video binaries use Git LFS;
- the publisher writes redacted metadata without private local paths or sensitive data.

## Review, approval, publish and cleanup lifecycle

Canonical lifecycle:

```text
VIDEO_READY
  -> REVIEW_PENDING
  -> Telegram/local preview
  -> exact output hash bound to review
  -> Owner decision
       |- revise -> invalidate dependent stages and resume
       |- cancel -> preserve according to retention policy
       `- approve -> APPROVED -> PUBLISH_PENDING
  -> deterministic Git/LFS publish
  -> verify remote commit + expected output hash/LFS object
  -> PUBLISHED
  -> CLEANUP_PENDING
  -> remove eligible local duplicates/intermediates
  -> ARCHIVED
```

Publishing before Owner approval is forbidden by default. Regeneration makes an earlier approval stale.

### Preview

Telegram should send the video directly when size/API policy permits. Otherwise provide a bounded private preview artifact/link or a smaller preview. The Owner should not need to inspect runtime directories manually.

Normal decisions stay small:

```text
通过并发布
重新生成
修改文稿
取消
```

### Post-publish cleanup

After verified successful remote publication, deterministic host cleanup may:

- remove duplicate local final MP4 copies safely represented by the verified Git LFS object;
- remove expendable per-slide audio, rendered pages, temporary PDF, video segments and transient encode files;
- retain compact manifests, hashes, state, review decision, publish commit and provenance;
- retain source material unless an explicit source-retention policy allows deletion;
- never remove PersonaProfile assets, raw persona/training datasets, models/adapters, credentials or unrelated files;
- never remove the only copy of an unpublished/failed artifact;
- preserve all required local artifacts on publish/verification failure;
- support explicit `保留本地` override.

Cleanup is bounded to known exact job/product paths. Model text cannot choose arbitrary delete paths.

## Job state model

General media jobs use durable stages such as:

```text
RECEIVED
INPUT_PENDING
REQUIREMENTS_PENDING
REQUIREMENTS_READY
MISSING_OWNER_FACT
SCRIPT_PENDING
SCRIPT_READY
PROFILE_SELECTED
ASSETS_READY
AUDIO_READY
VISUAL_READY
VIDEO_READY
REVIEW_PENDING
APPROVED
PUBLISH_PENDING
PUBLISHED
CLEANUP_PENDING
ARCHIVED
FAILED
CANCELLED
```

Every expensive stage is resumable and content-addressed. Changing script/scene content invalidates only dependent artifacts where possible. Telegram wizard state is durable across reconnect/restart.

## Security and trust boundary

Owner documents, PPT content, URLs, web pages, scripts, generated model text, voice recordings, images and video are data, not authority.

They cannot:

- widen identity/filesystem scope;
- execute arbitrary shell commands;
- access credentials;
- change Git authorization;
- enable cloud fallback;
- install/download models implicitly;
- create/qualify a PersonaProfile without deterministic validation and Owner policy;
- publish private persona/training assets;
- approve their own output;
- choose arbitrary cleanup paths.

Model-generated requirements/scripts/scene plans/prompts are validated host-side before they become filesystem, subprocess, training, publishing or cleanup actions.

## Capability status

### Ready now

- local Qwen3.8 narration/script generation from PPT content;
- Owner-provided final scripts through durable narration artifacts;
- deterministic Chinese/English routing;
- qualified persistent Chinese/English voices;
- explicit Owner reference-voice cloning;
- PPTX -> narration -> local TTS -> synchronized MP4;
- task-named `ai_video_product` Git LFS archival manually proven with real production outputs.

### Functional on the feature branch

- restart-safe, content-addressed MediaJob V0.2 and bounded private workspaces;
- upload/direct-brief/URL evidence intake contracts, with fetched content marked untrusted;
- task/brief -> local Qwen -> script/scene plan/prompt pack and deterministic no-PPT slides;
- first-class `--script-file`, `--brief-file` and `--url` CLI contracts;
- qualified persistent voice -> TTS -> timeline -> MP4;
- durable Owner Telegram `文件与媒体 -> 视频 -> 新建视频` workflow;
- exact-output approval, fixed-target Git/LFS publisher and verified cleanup;
- real local three-scene E2E through a temporary Git remote, ending in `ARCHIVED`.

### Partially ready

- Telegram production worker dispatch and direct preview transport remain undeployed; wizard state and interaction contracts are code-complete;
- custom persistent human voice: job-scoped reference cloning works, named reusable PersonaProfile does not yet;
- FLUX is downloaded but not yet qualified for production routing.

### Not yet implemented/qualified

- custom speaker fine-tuning/training pipeline;
- private persona dataset manager;
- visual identity LoRA/adapter training;
- identity-consistency evaluation suite;
- generative local video backend qualification;
- talking-avatar/lip-sync adapter;
- unified MediaJob expansion into image/video generation and training providers.

## Recommended implementation order

1. Merge/deploy Media Product Workflow V0.2 only after independent review and explicit approval.
2. Introduce private PersonaProfile + dataset/provenance store and reusable named custom voices.
3. Qualify already-downloaded Whisper and FLUX where current local dependencies permit; do not automatically install/download missing dependencies while network is constrained.
4. Add Training Plane schemas/state/evaluation/promote/rollback foundations.
5. Add visual identity adapters after image qualification.
6. Complete/qualify generative video after model download/network blockers clear.
7. Add lip-sync/talking avatar only after voice and visual identity profiles are independently qualified.

Material changes to this architecture follow `ARCHITECTURE_CHANGE_PROTOCOL.md` before implementation.
