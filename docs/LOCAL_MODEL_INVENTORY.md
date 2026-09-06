# Local Model Inventory

Generated: `2026-09-06T01:04:02.653316+00:00`

This is a live disk audit of the local model inventory on the 48 GB Mac.
It is intentionally read-only. No model was started, stopped, downloaded, or deleted.
Port 8199 was not accessed or modified.

## Summary

- Inventory candidates: **25**
- Queue-complete and marker-validated: **8**
- Queue partial/unverified: **0**
- Files present outside queue verification: **17**
- Total discovered payload: **262.140 GiB**
- Total `.incomplete` bytes inside discovered model directories: **0.000 B**

## Local `/Users/jerson/AI/models` inventory

| Model / directory | Queue role | State | Payload | Expected | Progress | Partial cache | Weights | Marker |
|---|---|---|---:|---:|---:|---:|---|---|
| `/Users/jerson/AI/models/flux2-klein-4b-bf16` | IMAGE_MAIN | QUEUE_COMPLETE_VALIDATED | 22.110 GiB | 22.110 GiB | 100.00% | 0.000 B | 5 safetensors / 22.087 GiB | VALID |
| `/Users/jerson/AI/models/flux2-klein-4b-bf16/text_encoder` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 7.493 GiB | — | — | 0.000 B | 2 safetensors / 7.492 GiB | no |
| `/Users/jerson/AI/models/flux2-klein-4b-bf16/tokenizer` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 15.146 MiB | — | — | 0.000 B | — | no |
| `/Users/jerson/AI/models/flux2-klein-4b-bf16/transformer` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 7.219 GiB | — | — | 0.000 B | 1 safetensors / 7.219 GiB | no |
| `/Users/jerson/AI/models/flux2-klein-4b-bf16/vae` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 160.333 MiB | — | — | 0.000 B | 1 safetensors / 160.333 MiB | no |
| `/Users/jerson/AI/models/longcat-video-q8` | VIDEO_HIGH | QUEUE_COMPLETE_VALIDATED | 31.315 GiB | 31.315 GiB | 100.00% | 0.000 B | 10 safetensors / 31.295 GiB | VALID |
| `/Users/jerson/AI/models/longcat-video-q8/dit` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 15.138 GiB | — | — | 0.000 B | 4 safetensors / 15.138 GiB | no |
| `/Users/jerson/AI/models/longcat-video-q8/lora` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 5.339 GiB | — | — | 0.000 B | 2 safetensors / 5.339 GiB | no |
| `/Users/jerson/AI/models/longcat-video-q8/text_encoder` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 10.582 GiB | — | — | 0.000 B | 3 safetensors / 10.582 GiB | no |
| `/Users/jerson/AI/models/longcat-video-q8/tokenizer` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 20.461 MiB | — | — | 0.000 B | — | no |
| `/Users/jerson/AI/models/longcat-video-q8/vae` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 242.050 MiB | — | — | 0.000 B | 1 safetensors / 242.049 MiB | no |
| `/Users/jerson/AI/models/mlx-community` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 19.026 GiB | — | — | 0.000 B | 4 safetensors / 19.001 GiB | no |
| `/Users/jerson/AI/models/mlx-community/Qwen3.6-35B-A3B-4bit` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 19.026 GiB | — | — | 0.000 B | 4 safetensors / 19.001 GiB | no |
| `/Users/jerson/AI/models/qwen3-embedding-8b` | EMBED | QUEUE_COMPLETE_VALIDATED | 14.110 GiB | 14.110 GiB | 100.00% | 0.000 B | 4 safetensors / 14.095 GiB | VALID |
| `/Users/jerson/AI/models/qwen3-embedding-8b/1_Pooling` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 313.000 B | — | — | 0.000 B | — | no |
| `/Users/jerson/AI/models/qwen3-reranker-8b` | RERANK | QUEUE_COMPLETE_VALIDATED | 15.267 GiB | 15.267 GiB | 100.00% | 0.000 B | 5 safetensors / 15.252 GiB | VALID |
| `/Users/jerson/AI/models/qwen3-reranker-8b/1_LogitScore` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 57.000 B | — | — | 0.000 B | — | no |
| `/Users/jerson/AI/models/qwen3-tts-base-bf16` | TTS_MAIN | QUEUE_COMPLETE_VALIDATED | 4.232 GiB | 4.232 GiB | 100.00% | 0.000 B | 2 safetensors / 4.228 GiB | VALID |
| `/Users/jerson/AI/models/qwen3-tts-base-bf16/speech_tokenizer` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 650.688 MiB | — | — | 0.000 B | 1 safetensors / 650.685 MiB | no |
| `/Users/jerson/AI/models/qwen3-tts-voice-design-bf16` | TTS_DESIGN | QUEUE_COMPLETE_VALIDATED | 4.210 GiB | 4.210 GiB | 100.00% | 0.000 B | 2 safetensors / 4.206 GiB | VALID |
| `/Users/jerson/AI/models/qwen3-tts-voice-design-bf16/speech_tokenizer` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 650.688 MiB | — | — | 0.000 B | 1 safetensors / 650.685 MiB | no |
| `/Users/jerson/AI/models/qwen38-27b-8bit` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 27.503 GiB | — | — | 0.000 B | 6 safetensors / 27.475 GiB | no |
| `/Users/jerson/AI/models/qwen38-27b-raw-8bit` | RAW_OWNER_ONLY | QUEUE_COMPLETE_VALIDATED | 27.500 GiB | 27.500 GiB | 100.00% | 0.000 B | 6 safetensors / 27.475 GiB | VALID |
| `/Users/jerson/AI/models/qwen38-27b-raw-8bit/8-bit` | unmanaged | FILES_PRESENT_NOT_QUEUE_VERIFIED | 27.500 GiB | — | — | 0.000 B | 6 safetensors / 27.475 GiB | no |
| `/Users/jerson/AI/models/whisper-large-v3-mlx` | STT_MAIN | QUEUE_COMPLETE_VALIDATED | 2.872 GiB | 2.872 GiB | 100.00% | 0.000 B | — | VALID |

## Queue targets

| ID | Role | Repository | Local path | Expected |
|---|---|---|---|---:|
| `stt-whisper-large-v3` | `STT_MAIN` | `mlx-community/whisper-large-v3-mlx` | `/Users/jerson/AI/models/whisper-large-v3-mlx` | 2.872 GiB |
| `tts-qwen3-base-bf16` | `TTS_MAIN` | `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16` | `/Users/jerson/AI/models/qwen3-tts-base-bf16` | 4.232 GiB |
| `tts-qwen3-voice-design-bf16` | `TTS_DESIGN` | `mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16` | `/Users/jerson/AI/models/qwen3-tts-voice-design-bf16` | 4.210 GiB |
| `image-flux2-klein-4b-bf16` | `IMAGE_MAIN` | `mlx-community/FLUX.2-klein-4B-bf16` | `/Users/jerson/AI/models/flux2-klein-4b-bf16` | 22.110 GiB |
| `embed-qwen3-8b` | `EMBED` | `Qwen/Qwen3-Embedding-8B` | `/Users/jerson/AI/models/qwen3-embedding-8b` | 14.110 GiB |
| `rerank-qwen3-8b` | `RERANK` | `Qwen/Qwen3-Reranker-8B` | `/Users/jerson/AI/models/qwen3-reranker-8b` | 15.267 GiB |
| `video-longcat-q8` | `VIDEO_HIGH` | `mlx-community/LongCat-Video-q8` | `/Users/jerson/AI/models/longcat-video-q8` | 31.315 GiB |
| `raw-qwen38-27b-8bit` | `RAW_OWNER_ONLY` | `orcarouter/Qwen3.8-27B-Uncensored-MLX` | `/Users/jerson/AI/models/qwen38-27b-raw-8bit` | 27.500 GiB |

## Production registry state

| Role | Profile | Registry status | Max context |
|---|---|---|---:|
| `MAIN` | `local-qwen38` | `QUALIFIED` | 16384 |
| `FAST` | `local-qwen36` | `VALIDATED` | — |
| `FALLBACK` | `local-qwen36` | `VALIDATED` | — |
| `VISION` | `local-qwen38` | `QUALIFIED` | — |
| `VIDEO_UNDERSTANDING` | `local-qwen38` | `REGISTERED_NOT_QUALIFIED` | — |
| `STT_MAIN` | `whisper-large-v3` | `REGISTERED_NOT_QUALIFIED` | — |
| `TTS_MAIN` | `qwen3-tts-base` | `REGISTERED_NOT_QUALIFIED` | — |
| `TTS_DESIGN` | `qwen3-tts-design` | `REGISTERED_NOT_QUALIFIED` | — |
| `IMAGE_MAIN` | `flux2-klein` | `REGISTERED_NOT_QUALIFIED` | — |
| `VIDEO_MAIN` | `wan21-video` | `REGISTERED_NOT_QUALIFIED` | — |
| `VIDEO_HIGH` | `longcat-video` | `REGISTERED_NOT_QUALIFIED` | — |
| `EMBED` | `qwen3-embedding` | `REGISTERED_NOT_QUALIFIED` | — |
| `RERANK` | `qwen3-reranker` | `REGISTERED_NOT_QUALIFIED` | — |
| `RAW` | `owner-qwen38-raw` | `REGISTERED_NOT_QUALIFIED` | — |

## Recorded workload qualification evidence

| Profile | Model | Workload | Status | Reason | Recorded date |
|---|---|---|---|---|---|
| `local-qwen36` | `mlx-community/Qwen3.6-35B-A3B-4bit` | `REPRESENTATIVE_WORKLOAD` | `PASS` | `QUALIFICATION_COMPLETE` | `2026-08-29` |
| `local-qwen38` | `mlx-community/Qwen3.8-27B-8bit` | `REPRESENTATIVE_WORKLOAD` | `BLOCKED` | `RELATIVE_SWAP_GROWTH_LIMIT` | `2026-08-29` |

## Other common local model caches

| Path | Total | Payload | Partial |
|---|---:|---:|---:|
| `/Users/jerson/.cache/huggingface/hub` | 271.000 B | 271.000 B | 0.000 B |
| `/Users/jerson/.cache/huggingface/hub/models--mlx-community--Qwen3.6-35B-A3B-4bit` | 40.000 B | 40.000 B | 0.000 B |
| `/Users/jerson/.cache/huggingface/hub/models--mlx-community--Qwen3.8-27B-8bit` | 40.000 B | 40.000 B | 0.000 B |

## Interpretation rules

- `QUEUE_COMPLETE_VALIDATED` means the configured completion marker matches the pinned repository/revision/expected size and the recorded payload files are present.
- `QUEUE_PARTIAL_OR_UNVERIFIED` means files exist but the configured queue completion proof is not currently valid.
- `FILES_PRESENT_NOT_QUEUE_VERIFIED` means model-like files are on disk but this audit cannot claim download completeness or runtime qualification.
- Download completeness does not imply runtime qualification.
- Registry status and workload qualification evidence are listed separately from disk presence.

## Safety

- `PORT_8199_TOUCHED=false`
- `MODELS_STARTED=false`
- `DOWNLOADS_STARTED=false`
- `FILES_DELETED=false`
