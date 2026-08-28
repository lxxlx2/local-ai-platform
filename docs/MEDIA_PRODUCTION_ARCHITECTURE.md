# Local media production architecture

Status: architecture baseline. Presentation narration/video V0.1 is implemented and locally qualified; generalized script ingestion, Telegram MediaJob UX, generative video, and trainable persona adapters remain staged work.

## Goal

The media plane turns either an Owner task or Owner-provided material into a reproducible local media product without making Codex or any cloud model a runtime dependency.

```text
Owner task / script / PPTX / future media brief
  -> deterministic intake + private job workspace
  -> optional local Qwen3.8 planning/script generation
  -> owner-provided or generated final script
  -> language detection + media routing
  -> audio / presentation / image / video adapters
  -> local render/composition
  -> Owner preview/review
  -> explicit approval
  -> deterministic product publishing
  -> verified local cleanup policy
```

The final script is a first-class durable artifact. A model may propose or generate it, but downstream TTS/rendering consumes the persisted final script rather than hidden conversational state.

## Supported input modes

The architecture supports four script origins:

1. `owner-script`: the Owner supplies the final narration text directly.
2. `presentation-notes`: speaker notes are the narration source.
3. `local-qwen-script`: the Owner supplies a task/brief and qualified local Qwen3.8 generates the script.
4. `hybrid`: Owner material is used where present; local Qwen3.8 fills explicitly missing sections.

First-class interfaces should expose script/brief files directly instead of requiring manual edits to `narration.json`.

Target interface:

```text
media prepare --task <name> --script-file <path>
media prepare --task <name> --brief-file <path> --script-generator local-qwen
presentation build --input <pptx> --script-file <path>
```

Generated scripts are persisted, editable, hashed, and independently reviewable before expensive synthesis.

## Current qualified presentation path

```text
PPTX
  -> safe parsing + render
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

The same TTS subsystem should be callable without a PPTX:

```text
final script
  -> language detection
  -> VoiceProfile
  -> safe sentence/paragraph chunking
  -> local Qwen3-TTS Base
  -> WAV/FLAC/MP3 output
  -> transcript/timing manifest
```

This path reuses the persistent VoiceProfile store and never recreates VoiceDesign references for every job.

## Generic task/script-to-presentation/video path

For presentation-like jobs, Qwen may receive only a task/brief and produce a structured script/scene plan first:

```text
Owner task
  -> local Qwen3.8
  -> durable draft script + scene/slide plan
  -> Owner review or explicitly qualified auto-continue policy
  -> final script
  -> render adapter
  -> TTS
  -> timeline
  -> MP4
```

Script-generation and media-generation are separate durable stages so the Owner can replace the script without regenerating unrelated artifacts.

## Telegram video intake

Telegram is the preferred simple Owner entry point for ordinary media work. It must not require command syntax, internal profile ids, filesystem paths, JSON, or Git knowledge.

The normal video flow is a step-by-step wizard reached through the existing media submenu:

```text
媒体
  -> 视频
  -> 新建视频
  -> 名称
  -> 材料方式
  -> 语言
  -> 声音/Persona
  -> 完成后处理
  -> 确认
  -> 生成
  -> 预览
  -> Owner 决策
```

Normal material choices:

```text
PPT + 文稿
只上传 PPT
只上传文稿
直接描述任务
```

Normal language choices:

```text
自动
中文
English
```

Normal voice choices use friendly labels and only expose qualified profiles by default:

```text
自动推荐
中文男声 25
English Male 25
我的声音/人物
```

Advanced model/profile identifiers remain hidden from the normal Telegram wizard. See `BOT_UX.md` for the canonical interaction details.

## PersonaProfile

A reusable human-like identity is represented as a private `PersonaProfile`. It is a routing contract, not a promise that every backend uses the same training method.

```text
PersonaProfile
  |- VoiceIdentityProfile
  |- VisualIdentityProfile
  |- optional Motion/VideoIdentityProfile
  |- provenance + consent metadata
  |- qualification state
  |- version/revision bindings
```

Each sub-profile may evolve from reference-based inference to a trained adapter/model without changing callers.

### VoiceIdentityProfile

Current capability:

- qualified default Chinese and English persistent voices;
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
  -> evaluation against held-out samples
  -> Owner approval
  -> qualified VoiceIdentityProfile revision
```

A future trained speaker backend is represented as a new profile revision. Existing jobs remain bound to the revision/hash used at generation time.

### VisualIdentityProfile

Current FLUX model availability is not equivalent to a qualified personal visual identity. Future flow:

```text
Owner-provided consented image/video material
  -> private dataset intake
  -> face/subject quality and duplicate checks
  -> captions/metadata
  -> baseline reference generation
  -> optional LoRA/adapter fine-tuning
  -> identity-consistency evaluation
  -> Owner approval
  -> qualified VisualIdentityProfile revision
```

Adapter-based training is preferred over modifying the shared base model for every person. The exact backend remains replaceable.

## Generative video

The qualified presentation-video pipeline is deterministic composition of rendered slides plus synthesized narration. It is distinct from generative video.

Future generative video path:

```text
script / scene plan
  -> local Qwen3.8 scene decomposition
  -> optional VisualIdentityProfile
  -> image/keyframe generation
  -> local video-generation backend
  -> optional voice track
  -> optional lip-sync / talking-avatar adapter
  -> FFmpeg composition
  -> MP4
```

A generative video backend is available only after the selected local model completes target-Mac qualification. Downloaded or queued alone is not READY.

## Training plane

Training is isolated from normal inference jobs.

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

Training jobs record:

- dataset manifest and hashes;
- explicit subject/profile id;
- consent/provenance note;
- base model id/revision;
- training code revision;
- hyperparameters/seed when supported;
- checkpoint hashes;
- evaluation outputs;
- Owner qualification decision.

Raw voice, face, and training-source material is private identity data and remains in an Owner-private local asset vault by default.

Suggested roots:

```text
/Users/jerson/AI/private/personas/<persona-id>/source/
/Users/jerson/AI/private/personas/<persona-id>/datasets/
/Users/jerson/AI/private/personas/<persona-id>/training/
/Users/jerson/AI/runtime/persona-profiles/<persona-id>/
```

These roots must never be included in the public product repository automatically.

## Product artifact publishing

Approved finished media products may be exported to:

```text
/Users/jerson/ai_video_product/<task-name>/
  source/
  output/
  metadata/
```

Target repository:

```text
lxxlx2/ai_video_product
```

Video binaries use Git LFS. Publishing is an explicit deterministic host action after successful generation and exact-output Owner approval; the language model itself never receives arbitrary Git mutation authority.

The public product repository may contain only materials approved for publication. Private persona recordings/images, raw training datasets, private checkpoints, credentials, and private intermediates never enter it automatically.

Target publisher:

```text
media publish --job <job-id> --task-name <slug> --repo ai_video_product
```

The publisher writes a redacted product manifest containing model/profile revisions and output hashes without leaking private local paths or training data.

## Review, approval, publish and cleanup lifecycle

The normal Owner lifecycle is:

```text
VIDEO_READY
  -> REVIEW_PENDING
  -> Telegram/local preview
  -> exact output hash bound to review
  -> Owner decision
       |- revise -> invalidate dependent stages and resume
       |- cancel -> preserve according to retention policy
       `- approve -> PUBLISH_PENDING
  -> deterministic Git/LFS publish
  -> verify remote commit + expected output hash
  -> PUBLISHED
  -> cleanup eligible local duplicates/intermediates
  -> ARCHIVED
```

Publishing before Owner approval is forbidden by default.

Approval must be bound to the exact candidate/output hash. Regeneration makes a prior approval stale.

### Preview

Telegram should deliver a direct video preview when Telegram/API/file-size policy permits. Otherwise it should provide a bounded private preview artifact/link. The user should not need to inspect runtime folders manually.

Normal decisions are deliberately small:

```text
通过并发布
重新生成
修改文稿
取消
```

### Post-publish cleanup

The platform should reduce local disk use, but cleanup is subordinate to durability and safety.

Default cleanup policy after verified successful remote publish:

- remove duplicate local final MP4 copies that are safely represented by the verified published Git LFS object;
- remove expendable per-slide audio, rendered pages, temporary PDF, video segments and transient encode files when the job no longer needs them;
- retain compact durable manifests, hashes, state, review decision, publish commit and provenance needed for audit/recovery;
- retain source material unless the Owner explicitly chose a source-retention policy allowing deletion;
- never remove PersonaProfile assets, raw persona/training datasets, model/adapters, credentials, or unrelated assets;
- never remove the only copy of an unpublished/failed-to-publish artifact;
- if publish verification fails, keep all required local artifacts and mark the job retryable;
- allow an explicit `保留本地` override before cleanup.

Cleanup itself is deterministic host code, bounded to the exact job workspace and known product-export copy. Model text cannot choose arbitrary deletion paths.

## Job state model

General media jobs use durable stages such as:

```text
RECEIVED
INPUT_PENDING
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

Every expensive stage is resumable and content-addressed. Changing the script invalidates dependent audio/video, but does not invalidate unrelated parsing or already-qualified persona profiles.

Telegram wizard state is also durable; reconnect/restart should resume the active question/job rather than restarting the workflow.

## Security and trust boundary

Owner documents, PPT content, scripts, retrieved web text, generated model text, voice recordings, images, and video are data, not authority.

They cannot:

- widen identity or filesystem scope;
- execute arbitrary shell commands;
- access credentials;
- change Git authorization;
- enable cloud fallback;
- install/download models implicitly;
- create or qualify a PersonaProfile without deterministic validation and Owner policy;
- publish private persona/training assets to public repositories;
- approve their own output;
- choose arbitrary cleanup/delete paths.

Model-generated script/scene plans are validated host-side before they become filesystem, subprocess, training, publishing, or cleanup actions.

## Capability status

### Ready now

- local Qwen3.8 script/narration generation from PPT content;
- Owner-provided final scripts through the durable narration artifact workflow;
- deterministic Chinese/English language routing;
- qualified persistent Chinese/English voices;
- reference-voice cloning for an explicit Owner-supplied WAV;
- PPTX -> narration -> local TTS -> synchronized MP4;
- task-named final-product archival in `ai_video_product` using Git LFS, currently proven through manual host commands.

### Partially ready

- task/brief -> local Qwen -> standalone final script: core local model exists, but no general MediaJob CLI/orchestrator yet;
- script-file -> presentation/audio: durable script artifacts exist, but first-class `--script-file` ingestion is not yet implemented;
- Telegram media entry exists architecturally, but the guided video wizard/review/publish flow is not implemented yet;
- custom persistent human voice: job-scoped reference cloning works, but named reusable custom-person profile creation/training is not implemented;
- generic local image generation: FLUX is downloaded but still needs its own local qualification before production routing.

### Not yet qualified/implemented

- standalone script-to-audio product CLI;
- automatic media artifact publisher into `ai_video_product`;
- verified post-publish local cleanup worker;
- Telegram exact-output preview/approval/publish callbacks;
- custom speaker fine-tuning/training pipeline;
- private persona dataset manager;
- visual identity LoRA/adapter training;
- identity-consistency evaluation suite;
- generative local video production backend qualification;
- talking-avatar/lip-sync adapter;
- one unified `MediaJob` orchestrator across script/audio/presentation/image/video/training/publishing.

## Recommended implementation order

1. Generalize script ingestion (`--script-file`, `--brief-file`) and standalone script-to-audio.
2. Introduce the durable `MediaJob` state model and deterministic publisher/cleanup lifecycle.
3. Implement the simple Telegram video wizard, preview and exact-output approval callbacks on top of MediaJob.
4. Introduce private `PersonaProfile` + dataset/provenance store; convert job-scoped voice reference into reusable named custom voice profiles.
5. Add optional voice fine-tuning backend behind `VoiceIdentityProfile` and qualification/evaluation.
6. Qualify already-downloaded Whisper and FLUX where local dependencies permit.
7. Add `VisualIdentityProfile` plus adapter/LoRA training after image qualification.
8. Qualify the selected local generative-video backend after its download completes, then add scene-plan -> video jobs.
9. Add talking-avatar/lip-sync only after voice and visual identity profiles are independently qualified.

This sequence preserves the already-qualified presentation path while progressively adding the broader media, persona and Telegram product capabilities.
