# ADR-0007: Isolated local music generation and reference reproduction

Status: ACCEPTED / IMPLEMENTING

Owner approval: 2026-09-11

Execution tracker: Issue #47

## Context

The platform needs a local music capability for full-song generation, reference-guided reproduction, cover-style regeneration, local repaint/editing, and later API integration. The immediate use case is to reproduce a user-owned reference song locally while preserving the existing text, vision, image, video, speech, retrieval, Telegram, and X workflows.

ACE-Step 1.5 is the first implementation target because the upstream project supports Apple Silicon through the MLX backend and exposes reference audio, cover generation, repaint/editing, audio understanding, metadata control, Gradio, and REST API workflows.

This capability is materially heavier than ordinary text or speech work and competes for the same unified memory used by browsers, IDEs, Unity, Qwen, FLUX, LongCat, and other desktop applications. It therefore inherits ADR-0005 workload-aware admission requirements.

The reference song and intermediate stems can also contain private or licensed media. They must not be treated as ordinary public Git artifacts.

## Decision

1. Add music as an isolated specialist capability named `MUSIC_MAIN`.
2. Stage the music registry separately from the production `model-registry-v0.1.json` until qualification is complete. Existing production aliases and routes remain unchanged.
3. First implementation target is ACE-Step 1.5 on Apple Silicon using the upstream MLX path.
4. Candidate model roles are:
   - `acestep-v15-xl-turbo` for faster iteration and seed exploration;
   - `acestep-v15-xl-sft` as the preferred final-quality candidate;
   - `acestep-v15-xl-base` only when base-only extraction/lego/complete features are needed.
5. No ACE-Step profile is production-qualified merely because it downloads, starts, or produces audio. Promotion requires task-specific evidence under the existing qualification policy.
6. Music runs on demand by default. No always-resident daemon is authorized by this ADR.
7. The music runtime must be isolated from existing Python/MLX environments. It must use its own checkout, dependency environment, model/cache root, logs, and output workspace.
8. The music service must bind to localhost by default. Any LAN/Tailscale exposure requires a separate explicit approval and access-control review.
9. Source/reference audio remains local by default. Public repositories may store safe metadata, hashes, prompts, configuration, scripts, and approved outputs, but may not automatically publish reference audio, stems, private recordings, or unapproved generated masters.
10. No silent paid or cloud fallback is allowed. A failed local route returns an explicit failure or waits according to workflow policy.
11. Existing `MAIN`, `FAST`, `FALLBACK`, `VISION`, `STT_MAIN`, `TTS_*`, `IMAGE_MAIN`, `VIDEO_*`, `EMBED`, `RERANK`, and `RAW` behavior must remain unchanged during the music implementation.
12. Music processes may be stopped only through exact-owned process identity. User applications must never be closed to obtain a qualification PASS.
13. Representative-workload resource failure is valid architecture evidence. The music route must yield, defer, downgrade, or remain unqualified rather than weaken system safety gates.
14. The reproducible song workflow and user-facing runbook live in `lxxlx2/ai_video_product` on the matching implementation branch. That repository is public, so source-media publication remains gated.

## Initial execution architecture

```text
Owner reference audio (local only)
        |
        v
reference preparation / validation
        |
        +--> optional local stem separation
        |
        v
ACE-Step 1.5 isolated MLX runtime
        |
        +--> Audio Understanding / metadata capture
        +--> Reference-guided generation
        +--> Cover generation
        +--> multi-seed candidate set
        +--> Repaint selected regions
        |
        v
local candidate WAV/FLAC artifacts
        |
        v
Owner listening / approval gate
        |
        +--> keep private/local
        +--> explicitly approve publication
```

The control plane may later wrap the ACE-Step REST API behind a provider adapter, but this ADR does not authorize production routing or service restart.

## Qualification phases

### Phase M0: install and isolation

- upstream checkout pinned to an exact commit or release;
- isolated environment installs successfully;
- model/cache/output paths remain outside Git;
- localhost UI/API can start and stop without touching existing services;
- port collision and process-ownership checks pass.

### Phase M1: functional reference reproduction

- ingest a user-owned reference file;
- preserve declared lyrics and target duration closely enough for evaluation;
- generate at least one reference-guided candidate and one cover candidate;
- record seed, model, LM, backend, duration, BPM/key metadata when available;
- prove deterministic cleanup of exact-owned processes.

### Phase M2: representative-workload qualification

- run with the Owner's normal workstation workload present;
- record unified-memory, swap, pressure, runtime, and responsiveness evidence;
- do not close browser/IDE/ChatGPT/Unity to manufacture headroom;
- classify result using the existing direct-work vocabulary.

### Phase M3: provider integration

Only after M0-M2 evidence is acceptable:

- add a control-plane provider adapter;
- add admission checks and explicit route state;
- keep music opt-in and on-demand;
- add focused tests proving no regression to existing providers;
- obtain required review and explicit Owner merge/deploy approval.

## Consequences

### Positive

- Full-song work can remain local and free of per-generation API charges.
- Reference reproduction becomes a durable, repeatable workflow.
- Existing production capabilities remain isolated from experimental music dependencies.
- Local source audio and stems do not leak into the public product repository by default.
- The system can later expose music through a controlled provider interface after evidence exists.

### Cost

- XL music models may have significant unified-memory pressure on a 48 GiB workstation.
- Apple Silicon behavior must be qualified directly; upstream VRAM guidance cannot be treated as proof of local coexistence.
- Exact reproduction of a generated commercial-model waveform is not guaranteed. The target is a close musical reproduction that can be iteratively improved with cover/reference/repaint workflows.
- Long generation jobs may need queueing or explicit admission when other heavy local models are active.

## Non-decisions

This ADR does not install ACE-Step, download model weights, modify a running service, change any existing provider route, expose a port beyond localhost, publish the reference song, grant commercial rights to any source or output, or mark `MUSIC_MAIN` qualified.
