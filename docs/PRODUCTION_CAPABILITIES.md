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

`AudioService`, `ImageService`, `VideoService`, `EmbeddingProvider`, and `RerankProvider` are dependency-free control contracts. Heavy inference implementations belong in their isolated runtime environments. `MediaJobRepository` persists only owner scope, state, bounded progress, private references, model role, and error category; raw prompts are excluded from its schema.

`SafeHttpFetcher` allows only HTTP(S), resolves and rejects every private/special address on every redirect, refuses URL credentials and nonstandard ports, bounds bytes/redirects/time, and accepts only uncompressed text/HTML/JSON. Web evidence is always marked `UNTRUSTED_EXTERNAL_CONTENT`. Browser execution remains Owner-only and separately configured.

## Deployment status

The Telegram navigation and deterministic routing are code-ready but are not deployed by this branch. Existing Bot and oMLX processes are not restarted as part of this work.
