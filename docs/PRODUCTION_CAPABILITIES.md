# Production Capability Consolidation V0.1

## Safety boundary

- Registered is not the same as installed, qualified, loaded, or ready.
- All model runtimes are isolated from `runtime/omlx-venv`.
- Only one heavy model may be resident. A switch unloads the current heavy model, performs a read-only memory preflight, loads the target, checks health, and rolls back on failure.
- RAW, browser automation, voice cloning, and image/video generation are Owner-only. Public privileges are unchanged.
- Model IDs can only come from `ModelRegistry`; Telegram input cannot inject a repository ID.
- Prompts and private media are not model-manager metadata and are never committed.

## Runtime roles

| Role | Candidate | State at code integration |
|---|---|---|
| MAIN / VISION | `mlx-community/Qwen3.8-27B-8bit` | Qualified on this 48 GB Mac; MAIN default/max is 16K, VISION passed independently; runtime eligibility comes from the strict versioned registry |
| VIDEO_UNDERSTANDING | `mlx-community/Qwen3.8-27B-8bit` | Registered; a separate video adapter qualification is required |
| FAST / FALLBACK | `mlx-community/Qwen3.6-35B-A3B-4bit` | Previously validated; live health is checked separately |
| RAW | `orcarouter/Qwen3.8-27B-Uncensored-MLX` (`8-bit/`) | Owner-only; not downloaded or qualified; refusal removal is never a security boundary |
| STT_MAIN | `mlx-community/whisper-large-v3-mlx` | Registered |
| TTS_MAIN | `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16` | Registered |
| TTS_DESIGN | `mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16` | Registered |
| IMAGE_MAIN | `mlx-community/FLUX.2-klein-4B-bf16` | Registered |
| VIDEO_HIGH | `mlx-community/LongCat-Video-q8` | Registered; model card recommends 48GB minimum, so qualification is strict |
| VIDEO_MAIN | Wan2.1 14B distilled MLX candidate | Exact stable repository still required |
| EMBED / RERANK | `Qwen/Qwen3-Embedding-8B`, `Qwen/Qwen3-Reranker-8B` | Registered, on-demand |

## Provider boundaries

`Qwen38Provider` talks only to a localhost sidecar on port 8001. Normal chat and Owner-private JPEG understanding use this provider; `/fast` and deterministic fallback use the existing localhost-only Qwen3.6 oMLX provider. A bounded async executor admits one heavy operation and runs the complete synchronous lifecycle plus inference in one dedicated worker, so the Telegram event loop remains responsive without holding a thread lock across an `await`. If an already-selected MAIN dies for an infrastructure reason, the factory stops and confirms MAIN down, then retries exactly once on FALLBACK. Context, validation, authorization, secret-firewall, and cancellation failures are never failover candidates, and one `ChatService` invocation owns history persistence.

The lifecycle owns only its two exact launchd labels, refuses simultaneous healthy heavy runtimes, performs both a 6 GiB absolute swap ceiling and a 2 GiB sampled-delta guard, and restores MAIN after a temporary FAST session (including a cold FAST request). Each owned launch records a mode-0600 identity binding PID, executable, exact argv, and start identity. Before a switch or inference failover, both that process identity becoming DEAD/MISMATCH and the fixed localhost endpoint becoming unavailable are required. A bootout error, unknown listener, invalid identity, or still-live partial start fails closed before the target or rollback runtime can start. PID reuse is unrelated and is never killed.

Owner Telegram images are downloaded and validated before heavy-runtime admission, bounded to 20 MB, checked by MIME, magic bytes, exact size, and symlink policy, then copied with mode 0600 into an ignored mode-0700 TTL spool. Each request spool file is deleted immediately after success, failure, or cancellation; TTL cleanup is crash recovery only. The provider accepts only paths below that spool root. Public image inference remains disabled and performs no download.

Qwen3.8 applies the 16,384-token ceiling to `chat_template(prompt) + output`. The sidecar uses the real tokenizer, reserves at least 16 output tokens, and clamps requested output to the exact remaining budget. The client performs only a coarse 4 MiB request-size guard because character count is not token count.

`AudioService`, `ImageService`, `VideoService`, `EmbeddingProvider`, and `RerankProvider` are dependency-free control contracts. Other heavy inference implementations belong in their isolated runtime environments. `MediaJobRepository` persists only owner scope, state, bounded progress, private references, model role, and error category; raw prompts are excluded from its schema.

`SafeHttpFetcher` allows only HTTP(S), resolves and rejects every private/special address on every redirect, refuses URL credentials and nonstandard ports, bounds bytes/redirects/time, and accepts only uncompressed text/HTML/JSON. Web evidence is always marked `UNTRUSTED_EXTERNAL_CONTENT`. Browser execution remains Owner-only and separately configured.

## Deployment status

The Telegram navigation and deterministic routing are code-ready but are not deployed by this branch. Existing Bot and oMLX processes are not restarted as part of this work.

## Manual model downloads

The pinned download queue is a manual, resumable manager with a default concurrency of three. It reserves disk globally across active models, gives each model an independent bounded retry, validates the exact revision snapshot before writing a completion marker, and never deletes Hugging Face partial cache. It is not launchd, cron, RunAtLoad, KeepAlive, or an automation.

- Start/resume: `control-plane/scripts/start-model-downloads.sh --parallel 3`
- Status once: `control-plane/scripts/status-model-downloads.sh`
- Watch interactively: `control-plane/scripts/watch-model-downloads.sh 5`
- Graceful stop: `control-plane/scripts/stop-model-downloads.sh`

The manager and each `hf download` child have private exact process identities. Stop signals only the verified manager; that manager stops only its own still-matching children and preserves every `.incomplete` file.
