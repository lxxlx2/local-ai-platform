# Presentation voice architecture

Status: implemented and locally qualified on the feature branch on 2026-08-28.

## Goal

Presentation/video narration should select a stable local voice from the actual narration language and reuse that voice across pages, jobs, and future workflows. Voice selection is deterministic host-side policy; presentation text cannot choose a more privileged model or change authorization.

```text
PPTX / narration text
  -> bounded language detection
  -> language policy router
  -> persistent voice profile
  -> local Qwen3-TTS Base synthesis
  -> per-slide WAV
  -> timeline/video composer
```

The platform must not independently run VoiceDesign for every slide. A default voice is created once, qualified, stored as a reusable profile, and then reused by the Base TTS voice-clone path for consistency.

## Persistent voice profiles

A `VoiceProfile` is a durable local asset and metadata record, not a new full language model by default. It may be backed by a qualified VoiceDesign-generated anchor/reference audio today and by a fine-tuned speaker model in a future version without changing callers.

Initial default profiles:

| profile id | language | intended voice | default use |
| --- | --- | --- | --- |
| `zh-male-25-default` | Chinese (`zh`) | approximately 25-year-old male; standard Mandarin; clear, natural, professional, medium pace | Chinese narration |
| `en-male-25-default` | English (`en`) | approximately 25-year-old male; neutral international English; clear, natural, professional, medium pace | English narration |

Initial implementation should create these profiles from the already-downloaded local Qwen3-TTS VoiceDesign model, persist the qualified reference WAV plus metadata, and synthesize normal narration with the already-downloaded Qwen3-TTS Base model. Profile generation is an explicit owner operation and must not happen implicitly on every presentation build.

Voice profile metadata must include at least:

- profile id and schema version
- language
- display description
- backend/model identity and revision
- reference/anchor WAV path and content hash
- reference transcript
- voice-design description when applicable
- sample rate/duration
- qualification status and timestamp
- optional future fine-tune/model reference

Generated profile assets live outside Git under the private runtime/model asset root. Git stores only configuration/schema and documentation, never generated voice WAV files.

## Language detection and routing

Language is selected from the final narration script, not merely from the PowerPoint UI language or filename.

Per slide, after the narration script is resolved:

1. deterministically inspect the bounded script text;
2. classify `zh`, `en`, `mixed`, or `unknown`;
3. select the configured profile for that language;
4. synthesize locally with Qwen3-TTS Base.

Default policy:

```text
zh      -> zh-male-25-default
en      -> en-male-25-default
mixed   -> dominant-language profile, with a persisted warning
unknown -> fail closed and require explicit --language or --voice-profile
```

For a normal presentation, determine a presentation-level dominant language first so a single stable voice is used throughout. Per-slide switching is allowed only with an explicit mixed-language mode because silent voice changes inside one presentation are undesirable.

The user may override automatic routing with an explicit `--language` or `--voice-profile`. Explicit overrides must still reference an existing qualified local profile.

## Script language

Narration modes remain independent of voice routing:

```text
notes   -> use speaker notes only
auto    -> generate narration from slide content with qualified local Qwen3.8
hybrid  -> notes when present, otherwise local Qwen3.8 generation
```

For `auto` and the generated portion of `hybrid`, narration generation should normally follow the dominant source/script language unless the owner explicitly requests a target language. Translation is a separate explicit transform; the TTS router must not silently translate text.

Example:

```text
English PPT / English notes -> English script -> en-male-25-default
Chinese PPT / Chinese notes -> Chinese script -> zh-male-25-default
Chinese PPT + explicit target=en -> local translation/narration step -> English script -> en-male-25-default
```

## Voice lifecycle

Recommended lifecycle:

```text
Owner creates/refreshes voice profile
  -> VoiceDesign generates candidate anchor WAV
  -> deterministic audio validation
  -> owner/qualification approval
  -> profile becomes QUALIFIED
  -> future jobs reuse that exact reference asset through Base TTS
```

A normal presentation build must not regenerate a qualified default profile. If a profile is missing or unqualified, the build reports a clear blocker or requires an explicit one-time profile creation step.

A future fine-tuned speaker model can replace the reference-audio backend behind the same profile id only through an explicit new profile revision and qualification. Existing jobs remain bound to the profile revision recorded in their manifest.

## User voice cloning

The architecture also supports an explicit owner-supplied voice reference. It creates a job-scoped or named private profile after validation and consent. It must never overwrite the language defaults implicitly.

## Presentation/video pipeline

```text
PPTX
  -> safe parse + speaker notes
  -> slide render
  -> narration resolution (notes/auto/hybrid)
  -> presentation language decision
  -> persistent VoiceProfile selection
  -> local Base TTS per slide
  -> real WAV duration
  -> slide timeline
  -> FFmpeg composition
  -> MP4 + narration/timeline manifests
```

All model inference is local by default. No cloud TTS fallback is permitted for private presentation content.

## Security and trust boundary

PPT content, speaker notes, generated scripts, and retrieved text are untrusted data. They cannot:

- change identity or authorization;
- select an Owner-only privileged route;
- create/overwrite voice profiles;
- access arbitrary files or credentials;
- execute shell/Git/service-control commands;
- enable cloud egress;
- cause automatic model downloads or package installation.

Language/profile routing is deterministic host-side code. Model text has no authority over it.

## Resumability and reproducibility

Every presentation job manifest should bind:

- source PPT hash;
- slide/script hashes;
- detected/selected language;
- selected voice profile id + revision/hash;
- TTS model identity/revision;
- generated WAV hashes and durations;
- renderer and video settings.

If only a script changes, regenerate the affected audio/video segment. If the voice profile changes, regenerate audio and dependent segments but reuse slide rendering. If only final encoding settings change, reuse slides and audio.

## Qualification policy

A voice profile is `QUALIFIED` only after real local audio generation and deterministic validation. A presentation-video pipeline is `READY` only after a real local PPTX -> narration -> profile routing -> TTS -> MP4 E2E succeeds on the target Mac.

Initial qualification must cover at least:

- Chinese script routes to `zh-male-25-default`;
- English script routes to `en-male-25-default`;
- unknown language fails closed without an override;
- mixed-language behavior is explicit and persisted;
- one profile is reused across multiple slides without per-slide VoiceDesign regeneration;
- generated WAV files are valid and non-empty;
- profile assets never enter Git;
- no cloud TTS fallback occurs.

## V0.1 implementation and Owner workflow

The implementation is in `local_ai_control.services.presentation_*` with a
narrow Owner CLI at `control-plane/scripts/presentation-video.sh`. Private
profiles are stored under `/Users/jerson/AI/runtime/voice-profiles/`; private
jobs are stored under `/Users/jerson/AI/runtime/presentation-jobs/`. Both roots
and their generated WAV, PNG, SRT, JSON, and MP4 artifacts are outside Git.

One-time default bootstrap:

```text
/Users/jerson/AI/control-plane/scripts/presentation-video.sh voice create-defaults
```

Normal automatic build:

```text
/Users/jerson/AI/control-plane/scripts/presentation-video.sh presentation build \
  --input "/absolute/path/to/file.pptx" \
  --narration hybrid \
  --language auto \
  --voice-profile auto \
  --output "/absolute/path/to/presentation.mp4"
```

The editable workflow is `presentation prepare`, inspect/edit the private
`narration.json`, then `presentation resume --job-id ...`. Script hashes are
recomputed from the edited text; only affected audio and video segments are
regenerated. A profile revision change invalidates dependent audio but not the
rendered slides. Translation happens only with explicit `--target-language`.

V0.1 uses fixed, shell-free local invocations: LibreOffice converts PPTX to
PDF, pdftoppm renders pages, MLX Audio 0.5.0 runs pinned local Qwen3-TTS models,
and FFmpeg emits H.264/AAC 30 fps yuv420p MP4. There is no cloud fallback and
no automatic model download. If system dependencies are absent, install them
explicitly with `brew install --cask libreoffice` and `brew install ffmpeg`.

Qualification evidence used a deterministic three-slide English PPTX. Local
Qwen3.8 produced three scripts, all routed to the single qualified
`en-male-25-default` revision 1 reference; Qwen3-TTS Base generated three real
WAV files; LibreOffice rendered three 1920x1080 slides; and FFmpeg produced a
36.254-second H.264/AAC MP4 against a 36.210-second duration timeline. An
unchanged resume preserved all WAV hashes and modification times. The source
PPTX hash was unchanged.

Media Product Workflow V0.2 reuses this qualified path. Its real-local
qualification generated a three-scene deterministic 16:9 deck, three English
narration WAVs with `en-male-25-default` revision 1, and a 50.728-second
H.264/AAC MP4. The harness bound approval to the exact MP4 SHA, published to a
temporary local Git/LFS remote, verified commit and output hash, applied
bounded cleanup, and reached `ARCHIVED`. No cloud or generative image/video
provider was used.
