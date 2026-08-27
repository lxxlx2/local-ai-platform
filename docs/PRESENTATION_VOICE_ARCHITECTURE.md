# Presentation voice architecture

Status: architecture contract for the feature branch. Implementation may lag this document until the presentation-video milestone is qualified.

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
