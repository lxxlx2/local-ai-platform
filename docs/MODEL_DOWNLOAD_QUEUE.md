# Model Download Queue V0.1

Status: QUEUE DESIGN + OPERATIONAL ENTRYPOINT

Latest audited download state is maintained in [`DOWNLOAD_STATUS.md`](DOWNLOAD_STATUS.md). Do not use old percentages from historical commits or Issue comments as current progress.

The queue downloads multiple models into `/Users/jerson/AI/models`, uses pinned revisions, preserves resumable Hugging Face local-dir caches, records runtime state outside Git, and requires separate hardware/runtime qualification after download.

Committed queue metadata lives in `config/model-download-queue-v0.1.json`. Runtime state, logs, completion markers, worker identity, locks and partial download data remain outside Git.

## Commands

Start/resume only after explicit approval and after the current config/runtime-state consistency checks pass:

```sh
/Users/jerson/AI/control-plane/scripts/start-model-downloads.sh
```

Read bounded status:

```sh
/Users/jerson/AI/control-plane/scripts/status-model-downloads.sh
```

The current status command is useful for manager/worker identity and byte accounting, but its `progress_pct` can be misleading for interrupted downloads because resumable `.incomplete` fragments may include duplicate attempts. Canonical human progress therefore uses:

- valid completion marker + snapshot validation for COMPLETE;
- completed payload percentage for incomplete targets;
- separate partial-cache GiB;
- explicit config/state drift warnings.

See `docs/DOWNLOAD_STATUS.md`.

## Completion contract

A download is COMPLETE only when the manager validates its completion marker and physical snapshot. Presence of many bytes, a Hugging Face cache, or a previous runtime `COMPLETED` string is insufficient on its own.

Downloaded does not mean qualified. Each model requires focused role-specific qualification before registry promotion or production use.

## Pause/resume safety

- `PAUSED` with zero verified workers means no active download work should be assumed.
- Stored PID/identity is historical unless exact live verification succeeds.
- Never broad-kill download processes.
- Do not delete `.incomplete` data solely to make progress accounting look cleaner.
- Before RAW resume, current queue config, runtime state, repo/revision and local directory must be reconciled.
- Network problems justify staying paused; they do not authorize target substitution or cache cleanup.

## Current audit summary

Audited 2026-08-29:

- manager PAUSED, active 0, quarantine 0, no download process;
- Whisper Large V3 COMPLETE/validated;
- Qwen3-TTS Base COMPLETE/validated;
- Qwen3-TTS VoiceDesign COMPLETE/validated;
- FLUX.2 klein 4B bf16 COMPLETE/validated;
- Embedding payload about 67.1%, with substantial resumable/duplicate partial cache;
- Reranker payload about 26.0%, with substantial resumable/duplicate partial cache;
- LongCat about 2.0%;
- configured RAW MLX target 0% in its configured directory;
- historical RAW Q6K runtime state/partial data conflicts with the currently configured RAW MLX target;
- about 586 GiB disk space free during audit.

The detailed paths, sizes and drift analysis are in `docs/DOWNLOAD_STATUS.md`.
