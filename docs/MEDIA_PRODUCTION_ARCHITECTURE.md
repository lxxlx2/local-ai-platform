# Local media production architecture

Status: architecture baseline. Presentation narration/video V0.1 is implemented and locally qualified; generalized script ingestion, generative video, and trainable persona adapters remain staged work.

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
  -> owner review
  -> explicit product publishing
```

The final script is a first-class durable artifact. A model may propose or generate it, but downstream TTS/rendering consumes the persisted final script rather than hidden conversational state.

## Supported input modes

The architecture supports four script origins:

1. `owner-script`: the Owner supplies the final narration text directly.
2. `presentation-notes`: speaker notes are the narration source.
3. `local-qwen-script`: the Owner supplies a task/brief and qualified local Qwen3.8 generates the script.
4. `hybrid`: Owner material is used where present; local Qwen3.8 fills explicitly missing sections.

Future CLI/API contracts should expose script files directly instead of requiring manual edits to `narration.json`.

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
  -> chunking at safe sentence/paragraph boundaries
  -> local Qwen3-TTS Base
  -> WAV/FLAC/MP3 output
  -> transcript/timing manifest
```

This path should reuse the persistent VoiceProfile store and must not recreate VoiceDesign references for every job.

## Generic script-to-presentation/video path

For presentation-like jobs, Qwen may receive only a task/brief and produce a structured script/scene plan first:

```text
Owner task
  -> local Qwen3.8
  -> durable script + scene/slide plan
  -> owner review or policy-qualified auto-continue
  -> render adapter
  -> TTS
  -> timeline
  -> MP4
```

The script-generation stage and media-generation stage are separate durable stages so the Owner can replace the script without regenerating unrelated artifacts.

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

A future trained speaker backend must be represented as a new profile revision. Existing jobs remain bound to the revision/hash used at generation time.

### VisualIdentityProfile

Current FLUX model availability is not equivalent to a qualified personal visual identity. A future visual identity flow should be:

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

The preferred architecture is adapter-based training (for example LoRA-style subject adapters) rather than modifying the shared base model for each person. The exact backend remains replaceable.

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

A generative video backend is considered available only after the selected local model has completed target-Mac qualification. A downloaded or queued model alone does not satisfy this requirement.

## Training plane

Training must be isolated from normal inference jobs.

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

Training jobs must record:

- dataset manifest and hashes;
- explicit subject/profile id;
- consent/provenance note;
- base model id/revision;
- training code revision;
- hyperparameters/seed when supported;
- checkpoint hashes;
- evaluation outputs;
- Owner qualification decision.

Raw voice, face, and training-source material is private identity data and must remain in an Owner-private local asset vault by default.

Suggested roots:

```text
/Users/jerson/AI/private/personas/<persona-id>/source/
/Users/jerson/AI/private/personas/<persona-id>/datasets/
/Users/jerson/AI/private/personas/<persona-id>/training/
/Users/jerson/AI/runtime/persona-profiles/<persona-id>/
```

These roots must never be included in the public product repository automatically.

## Product artifact publishing

Finished media products may be exported to the dedicated product repository:

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

Video binaries use Git LFS. Publishing is an explicit deterministic host action after successful generation/review; the language model itself never receives arbitrary Git mutation authority.

The public product repository may contain only materials approved for publication. Private persona source recordings/images, raw training datasets, private checkpoints, credentials, and private intermediate assets must never be copied there automatically.

A future publisher should support:

```text
media publish --job <job-id> --task-name <slug> --repo ai_video_product
```

and should write a redacted product manifest containing model/profile revisions and output hashes without leaking private local paths or training data.

## Job state model

General media jobs should use durable stages such as:

```text
RECEIVED
SCRIPT_PENDING
SCRIPT_READY
PROFILE_SELECTED
ASSETS_READY
AUDIO_READY
VISUAL_READY
VIDEO_READY
REVIEW_PENDING
COMPLETED
PUBLISHED
FAILED
```

Every expensive stage must be resumable and content-addressed. Changing the script invalidates dependent audio/video, but should not invalidate unrelated source parsing or already-qualified persona profiles.

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
- publish private persona/training assets to public repositories.

Model-generated script/scene plans are validated host-side before they are converted into filesystem, subprocess, training, or publishing actions.

## Capability status

### Ready now

- local Qwen3.8 script/narration generation from PPT content;
- Owner-provided final scripts through the durable narration artifact workflow;
- deterministic Chinese/English language routing;
- qualified persistent Chinese/English voices;
- reference-voice cloning for an explicit Owner-supplied WAV;
- PPTX -> narration -> local TTS -> synchronized MP4;
- task-named final-product archiving in `ai_video_product` using Git LFS (currently manual host commands).

### Partially ready

- task/brief -> local Qwen -> standalone final script: core local model exists, but no general media-job CLI/orchestrator yet;
- script-file -> presentation/audio: durable script artifacts exist, but first-class `--script-file` ingestion is not yet implemented;
- custom persistent human voice: job-scoped reference cloning works, but named reusable custom-person profile creation/training is not yet implemented;
- generic local image generation: a local image model is present in the platform plan, but the image-generation path still requires its own runtime qualification before it becomes a production dependency.

### Not yet qualified/implemented

- standalone script-to-audio product CLI;
- automatic media artifact publisher into `ai_video_product`;
- custom speaker fine-tuning/training pipeline;
- private persona dataset manager;
- visual identity LoRA/adapter training;
- identity-consistency evaluation suite;
- generative local video production backend qualification;
- talking-avatar/lip-sync adapter;
- one unified `MediaJob` orchestrator across script/audio/presentation/image/video/training/publishing.

## Recommended implementation order

1. Generalize script ingestion (`--script-file`, `--brief-file`) and standalone script-to-audio.
2. Add deterministic task-named product publishing to `ai_video_product`.
3. Introduce private `PersonaProfile` + dataset/provenance store; convert job-scoped voice reference into reusable named custom voice profiles.
4. Add optional voice fine-tuning backend behind `VoiceIdentityProfile` and qualification/evaluation.
5. Qualify the local image backend and add `VisualIdentityProfile` plus adapter/LoRA training.
6. Qualify the selected local generative-video backend and add scene-plan -> video jobs.
7. Add talking-avatar/lip-sync only after voice and visual identity profiles are independently qualified.
8. Unify all stages under a resumable `MediaJob` supervisor with explicit review/publish gates.

This sequence preserves the already-qualified presentation path while progressively adding the broader media and persona capabilities.
