# Model Download Status

Status: CANONICAL AUDITED DOWNLOAD SNAPSHOT

Last audited: 2026-08-29 local session

This file records the latest verified disk/runtime state. Live re-audit overrides this snapshot.

## Manager state

- Manager state: `PAUSED`.
- Active downloads: `0`.
- No download-related process was found during audit.
- Quarantine count: `0`.
- Stored manager PID `48535` is stale/unverified; `manager.pid` is absent while `manager.identity.json` and `state.json` still retain historical metadata.
- Disk free space at audit: about `586 GiB`.

## Valid completed downloads

Completion requires both a completion marker and snapshot validation.

| ID | Role | Payload | Marker | Validation |
|---|---|---:|---|---|
| `stt-whisper-large-v3` | STT_MAIN | 2.872 GiB | YES | PASS |
| `tts-qwen3-base-bf16` | TTS_MAIN | 4.232 GiB | YES | PASS |
| `tts-qwen3-voice-design-bf16` | TTS_DESIGN | 4.210 GiB | YES | PASS |
| `image-flux2-klein-4b-bf16` | IMAGE_MAIN | 22.110 GiB | YES | PASS |

These are physically downloaded. They are not automatically production-qualified.

## Paused/incomplete targets

Canonical progress uses completed payload percentage plus separate partial-cache size. Do not use `(payload + .incomplete cache) / expected` as a meaningful completion percentage because repeated interrupted attempts can leave duplicate partial fragments.

| ID | Expected | Completed payload | Payload % | Partial cache | Marker |
|---|---:|---:|---:|---:|---|
| `embed-qwen3-8b` | 14.110 GiB | 9.469 GiB | ~67.1% | 10.723 GiB | NO |
| `rerank-qwen3-8b` | 15.267 GiB | 3.969 GiB | ~26.0% | 23.633 GiB | NO |
| `video-longcat-q8` | 31.315 GiB | 0.625 GiB | ~2.0% | 0 GiB | NO |
| configured `raw-qwen38-27b-8bit` | 27.500 GiB | 0 GiB | 0% | 0 GiB in configured directory | NO |

The current status command clamps Embedding and Reranker to `99.90%` because it credits partial-cache bytes. That number is operationally useful only as “resumable bytes exist”; it is misleading as human completion progress and must not be treated as canonical progress.

## RAW model configuration drift

A blocking consistency issue exists before RAW download resume:

- Current queue configuration targets:
  - id `raw-qwen38-27b-8bit`
  - repo `orcarouter/Qwen3.8-27B-Uncensored-MLX`
  - local dir `/Users/jerson/AI/models/qwen38-27b-raw-8bit`
- Stored runtime state still contains historical target:
  - id `raw-qwen38-27b-q6k`
  - repo `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`
  - local dir `/Users/jerson/AI/models/qwen38-owner-raw-q6k`
- An `.incomplete` file exists in the historical Q6K directory.
- Separate `.incomplete` files also exist under `/Users/jerson/AI/models/qwen38-27b-8bit`, which is outside the configured auxiliary RAW target and must not be automatically deleted.

Before any RAW resume, explicitly choose the intended model/runtime target, reconcile queue config/state/local directory naming, and preserve or intentionally retire old partial data. Do not let the manager silently interpret historical state as the new target.

## Resume rules

Network conditions currently block useful progress. Until resume is explicitly authorized:

- keep manager paused;
- do not delete `.incomplete` data merely because progress percentages look wrong;
- do not start an alternate RAW target automatically;
- do not treat completed download as qualification;
- when resuming, first normalize RAW configuration drift and improve human-facing progress reporting.

## Next download-related engineering tasks

1. Change status output to report `payload_pct` and `partial_cache_gib` separately; avoid presenting 99.9% as canonical completion for duplicate partial-cache-heavy downloads.
2. Reconcile RAW configured target vs historical runtime state.
3. Clear stale manager PID metadata safely when manager is confirmed absent, without broad process control.
4. When network is usable, resume only explicitly selected targets.
5. Qualify already-complete Whisper, TTS and FLUX independently of the remaining downloads when roadmap sequencing permits.
