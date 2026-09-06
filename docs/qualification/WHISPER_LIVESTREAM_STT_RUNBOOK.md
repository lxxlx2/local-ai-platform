# Whisper large-v3 LIVESTREAM_STT runbook

Status: EXECUTION READY

Tracker: Issue #44

This runbook validates whether the installed `mlx-community/whisper-large-v3-mlx` model can directly support the livestream/video clipping workflow on the Owner's normal 48 GB Mac workstation.

## Test inputs

Prepare three real, user-owned media clips:

1. one Chinese speech clip;
2. one English speech clip;
3. one noisy or mixed clip representative of the intended clipping workflow.

Video and audio containers supported by local ffmpeg are acceptable, for example MP4, MOV, M4A, MP3 or WAV.

Do not close Chrome, Codex/ChatGPT, IDEs or other normal work applications merely to make the test pass.

## One-shot command

First update the local repository:

```sh
cd /Users/jerson/AI
git checkout main
git pull --ff-only origin main
```

Set the three file paths. Dragging a file from Finder into Terminal is fine:

```sh
ZH="/absolute/path/to/chinese-clip.mp4"
EN="/absolute/path/to/english-clip.mp4"
NOISY="/absolute/path/to/noisy-workflow-clip.mp4"
```

Run the full model test:

```sh
/Users/jerson/AI/runtime/control-plane-venv/bin/python \
  /Users/jerson/AI/control-plane/scripts/validate-whisper-livestream-stt.py \
  --zh "$ZH" \
  --en "$EN" \
  --noisy "$NOISY"
```

## What the harness does

The harness:

- verifies the installed model and isolated `audio-venv` runtime;
- verifies `ffmpeg`/`ffprobe` and `mlx_whisper` without installing anything;
- captures a `REPRESENTATIVE_WORKLOAD` manifest;
- runs the repository `MemoryPreflight` for the 6 GiB Whisper profile;
- starts one exact-owned transcription worker in its own process group;
- keeps the Hugging Face and Transformers paths offline so the test cannot silently download another model;
- transcribes all three real clips in one worker process with word timestamps;
- records runtime and real-time factor for each clip;
- samples memory pressure, reclaimable memory and swap during execution;
- stops only its own worker if the repository resource gates are violated;
- writes TXT and SRT transcript artifacts into private runtime evidence;
- asks the Owner to judge meaning, names/numbers and timestamp usability for each clip;
- emits a safe Git summary with hashes and metrics, excluding raw media and transcript text.

The harness does not access unrelated local ports, does not close user applications, does not install packages and does not commit private media.

## Final statuses

The run ends in one of the direct-work statuses defined by `MODEL_WORK_CAPABILITY_VALIDATION_V1.md`:

- `RESOURCE_BLOCKED`
- `FUNCTIONAL_FAIL`
- `QUALITY_FAIL`
- `NOT_TESTED` when run with `--non-interactive`
- `WORKFLOW_PASS`

`WORKFLOW_PASS` requires all three inputs to produce non-empty timestamped transcripts, resource and cleanup gates to pass, and all Owner quality questions to be answered `y`.

## Evidence location

Private evidence is written under:

```text
/Users/jerson/AI/runtime/direct-work-validation/whisper-large-v3/<run-id>/
```

The important files are:

```text
result.json
preflight.json
workload-manifest.json
resource-samples.json
zh.transcript.txt
en.transcript.txt
noisy.transcript.txt
zh.srt
en.srt
noisy.srt
git-summary.md
```

Only `git-summary.md` content, hashes and safe metrics are intended to be synchronized to Git. Raw media and raw transcripts stay outside Git.

## After the run

Send the terminal output from `===== FINAL RESULT =====` through the end of `===== SAFE GIT SUMMARY =====` back to the project conversation. The result must then be synchronized to Issue #44 and the direct-work capability ledger before moving to the next model.
