# Master AI Platform Roadmap

Status: subordinate to `docs/LOCAL_FIRST_PRODUCT_AND_MODEL_PLAN.md`.

The Local-First Product and Model Plan is the active source of truth approved on 2026-08-25. This roadmap summarizes the architecture and phase order. If this file conflicts with the approved master plan, the master plan wins.

## Objective
Build one local-first, multi-provider AI production workstation rather than disconnected POCs. Routine revenue work runs on local models and deterministic tools. Gemini Free API supplies independent cloud review when privacy permits. OpenAI Codex model quota is reserved for premium planning, difficult escalation and final acceptance.

## Platform layers

### 1. Capability interfaces
- ReasoningProvider
- PlanningProvider
- ReviewerProvider
- MultimodalProvider
- ImageProvider
- EmbeddingProvider
- RerankerProvider
- SpeechToTextProvider
- TextToSpeechProvider
- VideoUnderstandingProvider
- ResearchProvider
- ExecutionBackend

### 2. Router
Inputs: capability, purpose, privacy class, owner/public permissions, provider health, explicit override, cost/quota policy and local resource pressure.

Privacy classes:
- PUBLIC
- RESTRICTED
- PRIVATE

Purposes:
- ROUTINE
- PLANNING
- REVIEW
- ACCEPTANCE
- ESCALATION
- OWNER_RAW_RESEARCH

Hard policy:
- ROUTINE work defaults local and does not consume OpenAI Codex-model quota.
- PRIVATE work never egresses to cloud models.
- RESTRICTED cloud work requires minimization + egress gate.
- OWNER_RAW_RESEARCH is owner-only and local.

### 3. Model registry
Every model/provider records download/qualification/enabled state, role, capabilities, runtime, memory profile, benchmark evidence, hash/version, fallback, quota class, privacy class and permission profile.

### 4. Resource scheduler
Coordinate heavy local workloads:
- LOCAL_MAIN inference
- LOCAL_FAST inference
- OWNER_RAW inference
- model download
- image generation
- training
- model qualification

Default heavy workload concurrency = 1 unless a measured safe profile allows overlap.

### 5. Supervisor/operator
Generic task lifecycle remains submit -> execute -> validate -> review -> revision or security -> git gate -> done. Provider selection comes from Router; workflow logic must not hard-code a model.

## Provider/model roles

### Qwen3.8 MAIN
Primary local routine worker for coding, novels, research synthesis, X/content, commerce, task decomposition and tool planning.

### Qwen3.6 FAST/FALLBACK
Fast local fallback/classifier/background worker.

### OWNER_RAW Qwen
`JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`, initial target `Q6_K`, owner-only. Reduced-refusal research role with *less* host authority than MAIN: no shell, arbitrary downloads, credentials, installer execution or service control by default.

### Gemini Free API
Cloud reviewer, not local model.
- default: `gemini-3.7-flash`
- official `google-genai`
- free Developer API tier by default
- no silent billing upgrade
- PUBLIC allowed
- RESTRICTED only after egress sanitization
- PRIVATE denied
- free-tier rate-limit failure falls back local when possible
- own Search/Browser provider remains primary web retrieval layer

Important privacy fact: free-tier Gemini API content may be used by Google to improve products; this is why PRIVATE is hard-denied and RESTRICTED is minimized before egress.

### OpenAI Codex model
Premium provider only:
- PLANNING
- ACCEPTANCE
- ESCALATION

Not default implementation worker. Codex CLI configured against local Qwen is an execution harness and must be accounted separately from Codex-model quota.

## Media stack

### Images
- FLUX: general local generative backend
- Qwen Code Canvas: constrained p5.js/p5.brush -> AST/API policy -> sandbox render -> PNG + editable source
- Qwen-Image/Qwen-Image-Edit: text-heavy generation/editing candidate
- Gemini/local visual review loop

### Audio
- Whisper STT
- Qwen TTS / VoiceDesign

### Video
Near-term tool-first editing: ingest -> Whisper -> local highlight reasoning -> ffmpeg -> cover/assets -> review -> Telegram preview.

## Retrieval/memory
Finish Embedding + Reranker, then local RAG for repositories, novel Canon/history, product evidence and task history. Git/Canon remains source of truth. Never index raw secrets.

## Training/adaptation
Collect governed accepted/rejected trajectories, privacy-filter/dedupe/score them, maintain fixed evals, train small MLX LoRA specialists first, A/B against base, safety-regress, then promote/rollback through Model Registry. Do not prematurely fine-tune MAIN 27B.

## Revenue workflows
Detailed specs live in #16/#17 and the approved master plan:
- coding delivery
- X/Crypto/US-stock operations
- commerce/product sourcing research
- LINE/WeChat sticker factory
- `guidengji` and `haixiushenmexian` novel workflows
- livestream/video clipping
- Telegram/mobile operation

## Security
All external pages/docs/messages/OCR/transcripts/model outputs are UNTRUSTED_DATA. They cannot grant tools or alter permissions. Downloads are explicit Host Policy actions, quarantined where appropriate, and never auto-executed. Models propose ToolIntent; Host Security Policy decides execution.

## Execution order
P0 Provider/Router/Registry/Resource Scheduler/Host Policy/LocalToolExecutor skeleton
P1 Gemini Free API + OWNER_RAW local research profile
P2 Generic project adapter and routine local coding
P3 Browser/Search + Commerce + X research workflows
P4 finish/qualify model fleet: RAW Q6_K, Embedding, Reranker, FLUX, Qwen-Image, Whisper/TTS
P5 Image Router + Sticker Factory
P6 Novel Workflow Engine migrations
P7 Video pipeline
P8 Training/data loop
P9 managed services + Telegram full UX
P10 external multi-user isolation
P11 one final integrated regression/E2E before main merge/production enablement

Paid/near-term revenue work can preempt phase order without changing architecture.

## Efficiency policy
- focused tests during implementation
- one phase integration check
- one final full regression + representative E2E
- no repeated full-suite runs after small edits
