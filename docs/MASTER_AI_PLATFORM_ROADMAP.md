# Master AI Platform Roadmap

Source of truth: GitHub Issue #14.

## Objective
Build one multi-provider local AI workstation rather than a sequence of disconnected POCs. Supervisor/Operator must sit above stable provider interfaces so local Qwen, Gemini, future local specialists, image generation, retrieval and speech can be added without rewriting task orchestration.

## Platform layers

### 1. Capability interfaces
- ReasoningProvider
- ReviewerProvider
- MultimodalProvider
- ImageProvider
- EmbeddingProvider
- RerankerProvider
- SpeechToTextProvider
- TextToSpeechProvider

### 2. Router
Inputs: task type, privacy class, provider health, explicit user override, cost/latency policy and local resource pressure.

Privacy classes:
- PUBLIC
- RESTRICTED
- PRIVATE

### 3. Model registry
Every model/provider records:
- downloaded
- qualified
- enabled
- role/capabilities
- memory/resource profile
- benchmark evidence
- current version/hash
- fallback order

### 4. Resource scheduler
Heavy local workloads are mutually coordinated. Profiles include:
- MAIN inference
- FAST inference
- model download
- image generation
- training
- model qualification

### 5. Supervisor/operator
Generic task lifecycle remains:
submit -> producer -> validation -> review -> revision or security -> git gate -> done.
The selected provider is supplied by the Router, not hard-wired into workflow logic.

## Provider roles

### Local Qwen
- Qwen3.8 MAIN/private producer/reasoner
- Qwen3.6 FAST/FALLBACK
- future local LoRA specialists

### Gemini
First-class secondary provider:
- independent code and architecture review
- large-context analysis
- image/PDF/video/audio multimodal analysis
- diagnosis after repeated producer failures
- optional explicit planner mode later

Gemini has no direct shell, Git write, merge, deploy or service-control authority. Cloud egress passes the privacy gate.

### Codex
Execution/tool shell and mutation agent. External findings must be checked against repository source/tests/docs.

## Gemini implementation
- official google-genai SDK
- structured output contract
- direct Gemini provider in platform
- ReviewerProvider + MultimodalProvider
- privacy/egress gate
- Codex-facing MCP STDIO adapter
- timeout/rate limit/model unavailable/content blocked error mapping
- optional provider override from operator

## Generic project support
- explicit authorized local Git repo or clone source
- isolated non-main worktree per task
- bounded project profile (test command, allowed paths, privacy class)
- provider selection via Router
- stop at REVIEW_RESULT_PENDING by default

## Model fleet
Last known state before live verification:
- Qwen3.8 MAIN: qualified/running
- Qwen3.6 FAST: qualified, normally stopped
- Whisper: downloaded, qualification pending
- TTS: downloaded, qualification pending
- FLUX: ~60.09%
- Embedding: ~40.66%
- Reranker: ~55.65%
- LongCat/raw auxiliary Qwen3.8 items: 0%
- downloads paused, active=0

Image targets:
- FLUX near-term backend
- Qwen-Image and Qwen-Image-Edit family
- Qwen Code Canvas for editable/code-generated visual assets
- Gemini vision review loop

## Retrieval
Finish embedding and reranker downloads, then add local RAG/document/repository retrieval. Do not index secrets or uncontrolled runtime state.

## Speech
Qualify Whisper and TTS and expose STT/TTS providers to operator/Telegram.

## Local training
Build the pipeline now, train later when clean data exists:
1. collect accepted task/revision/review trajectories
2. privacy filter, dedupe, quality score
3. fixed eval corpus
4. MLX LoRA path for smaller specialist models
5. register candidate
6. A/B benchmark vs base
7. safety regression
8. promote/rollback

Do not destabilize the 27B MAIN model by fine-tuning it without evidence. First targets should be smaller specialist/router/reviewer models.

## Execution phases
- P0 provider interfaces + router + registry + resource scheduler
- P1 Gemini direct provider + privacy gate + MCP reviewer
- P2 Generic Project Adapter
- P3 resume/download/qualify remaining model fleet
- P4 multimodal: FLUX + Qwen-Image evaluation + Canvas + Gemini vision review
- P5 embedding/reranker/RAG
- P6 Whisper/TTS
- P7 training pipeline + first specialist LoRA
- P8 daemon/service management + Telegram UI
- P9 final integrated regression/E2E + review before main merge

## Efficiency rule
During implementation, run focused tests only. Each phase gets one integration check. The platform gate gets one full control-plane regression and one real integrated E2E. Do not repeat full-suite or speculative pre-tests for every small change.
