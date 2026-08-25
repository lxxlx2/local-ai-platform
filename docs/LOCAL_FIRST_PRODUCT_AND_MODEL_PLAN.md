# Local-First Product and Model Plan

Status: ACTIVE MASTER EXECUTION PLAN

Related issues: #14 infrastructure roadmap, #15 product end-state, #16 novel workflow engine, #17 revenue workflows.

## 1. Product objective

Build a local-first AI production workstation whose routine revenue work runs on local models and deterministic local tools. Cloud/model quota is used only where it adds disproportionate value.

Primary commercial workflows:
- software/client coding delivery
- LINE / WeChat sticker production
- X/Twitter finance + Crypto account operations
- product sourcing/research across FB and other sites
- long-form novel production for `guidengji` and `haixiushenmexian`
- livestream/video clipping and editing
- research/RAG/content operations
- Telegram remote task submission/review
- later restricted external multi-user service

The system is not a model demo. Every platform feature should map to revenue, quality, user time saved, safety, or future model portability.

## 2. Hard routing policy

### Routine execution
Default worker: LOCAL.

Routine production must not consume OpenAI Codex model quota when a qualified local capability exists.

Examples:
- code implementation/debug loops
- novel planning/drafting/continuity work
- X research synthesis/drafting
- commerce research synthesis
- sticker/image orchestration
- video transcription/highlight/edit planning
- RAG/retrieval
- recurring monitoring/task processing

### Codex model
Reserve OpenAI Codex model quota for:
1. high-value architecture/planning;
2. difficult diagnosis/escalation after local attempts fail;
3. final acceptance/verification for important deliverables.

Codex is not the default production worker.

A Codex CLI process configured to use the local Qwen provider is treated as a local execution harness, not as Codex-model consumption. The platform must record which provider/model actually generated each turn so this distinction is auditable.

Long term, routine execution should use a direct Local Tool Executor so local workflows do not depend on Codex CLI at all.

### Gemini
Gemini is the preferred independent cloud reviewer/second-opinion provider when privacy permits.

Gemini roles:
- architecture review
- code review
- creative/editorial review
- long-context review
- image/PDF/video/audio multimodal review
- repeated-failure diagnosis

Gemini does not own shell/Git/file-write/deploy/service permissions and cannot silently promote novel Canon or approve irreversible actions.

### Private tasks
PRIVATE tasks never egress to Gemini/Codex cloud models. They use local reviewer roles.

## 3. High-level architecture

```text
Telegram / Local UI / API
          |
      Task System
          |
      Supervisor
          |
  Capability Router
          |
  +-------+--------+----------------+----------------+
  |                |                |                |
Local Reasoning  Local Media     Local RAG       Cloud Review
Qwen             Image/Audio     Embed/Rerank    Gemini
  |                |                |                |
  +----------------+--------+-------+----------------+
                           |
                    Host Tool Policy
                           |
       Git / Files / Browser / ffmpeg / Render / Search
                           |
                    Durable Artifacts
```

OpenAI Codex model is an escalation/planning/acceptance provider above this routine path, not the default worker.

## 4. Provider/capability interfaces

Platform interfaces:
- `ReasoningProvider`
- `PlanningProvider`
- `ReviewerProvider`
- `CodeReasoningProvider`
- `MultimodalProvider`
- `ImageProvider`
- `EmbeddingProvider`
- `RerankerProvider`
- `SpeechToTextProvider`
- `TextToSpeechProvider`
- `VideoUnderstandingProvider`
- `ExecutionBackend`

Every workflow asks for a capability. It does not hard-code a model name.

Router inputs:
- capability
- task purpose: ROUTINE / PLANNING / REVIEW / ACCEPTANCE / ESCALATION
- privacy: PUBLIC / RESTRICTED / PRIVATE
- project policy
- model/provider health
- local memory/resource pressure
- user override
- quota/budget policy

Router output records:
- selected provider
- selected model/profile
- why it was selected
- whether external quota can be consumed
- privacy decision
- fallback chain

## 5. Model design

### A. Local language/reasoning

#### Qwen3.8 27B 8-bit
Role: `LOCAL_MAIN`.

Use for:
- routine reasoning
- coding planning and iterative repair
- novel Plot/World/Character/Writing roles
- research synthesis
- task decomposition
- local multimodal/vision tasks within qualified limits

Current status: qualified MAIN, 16K qualified context, local runtime already proven with tool loop.

#### Qwen3.6 35B-A3B 4-bit
Role: `LOCAL_FAST_FALLBACK`.

Use for:
- fast/cheap local work
- fallback when MAIN unavailable
- parallel logical-agent throughput where quality allows
- possible local independent review

Current status: qualified, normally stopped.

#### Future local specialists
Train/promote smaller models for:
- router/task classifier
- novel continuity/style review
- commerce evidence/ranking
- X content ranking
- code review
- prompt rewriting

Do not destabilize Qwen3.8 MAIN with premature fine-tuning.

### B. Independent cloud reviewer

#### Gemini
Role: `CLOUD_REVIEWER_MULTIMODAL`.

Interfaces:
- ReviewerProvider
- MultimodalProvider
- optional PlanningProvider for explicit user requests

Privacy:
- PUBLIC: allowed
- RESTRICTED: only after minimization + secret/PII egress gate
- PRIVATE: denied

Codex-facing integration: MCP STDIO tools such as `review_code`, `review_architecture`, `ask`, `multimodal`.
Supervisor-facing integration: direct provider interface using the same privacy gate.

### C. Codex model

Role: `CLOUD_PREMIUM_PLANNER_ACCEPTOR`.

Allowed default purposes:
- PLANNING
- ACCEPTANCE
- ESCALATION only after policy threshold

Routine production purpose is denied unless the user explicitly overrides policy.

Track actual model quota usage separately from Codex CLI process usage.

### D. Image stack

#### FLUX
Role: general generative image backend.
Use for ordinary illustration, realistic/general visual generation.

#### Qwen Code Canvas
Role: editable deterministic visual backend.
Flow:
Qwen -> constrained p5.js/p5.brush code -> AST/API validation -> sandboxed browser renderer -> PNG + retained editable source.
No network, filesystem, eval or dynamic import inside generated drawing code.

Best for:
- infographics
- icons
- stickers
- posters
- line art
- layouts requiring precise later edits

#### Qwen-Image / Qwen-Image-Edit
Role: text-heavy generation/editing candidate backend.
Evaluate suitable Apple-Silicon/quantized variant before committing to a runtime profile.

#### Reviewer loop
Qwen/image backend -> render -> Gemini Vision or local reviewer -> revise -> render -> user approval.

### E. Speech/audio

#### Whisper large-v3 MLX
Role: STT_MAIN.
Use for video/livestream transcription, searchable media, subtitles.

#### Qwen3-TTS Base / VoiceDesign
Roles: TTS_MAIN / TTS_DESIGN.
Use for narration, creative audio, future media workflows.

### F. Retrieval

#### Qwen3 Embedding 8B
Role: EMBEDDING.

#### Qwen3 Reranker 8B
Role: RERANK.

Use for:
- repository retrieval
- novel Canon/history retrieval
- research memory
- product evidence retrieval
- task/history lookup

Git/Canon remains source of truth. Vector/RAG index is a retrieval accelerator only.

### G. Video

Near-term editing is deterministic tool-first:
- Whisper transcript
- local reasoning for highlights
- ffmpeg for cut/stitch/crop/subtitles/overlays
- image providers for covers

LongCat/Wan/local video generation remain optional provider targets after core editing is productive.

## 6. Execution backend design

### Phase 1
Reuse the already-qualified Qwen -> Codex CLI custom provider loop when useful. Because the CLI is configured to talk to local Qwen, this path must not invoke OpenAI Codex model quota.

### Phase 2
Introduce `LocalToolExecutor` owned by Supervisor:
- bounded shell commands
- path allowlists
- Git worktree policy
- process/time/memory limits
- no ambient network by default
- explicit browser/search tools
- immutable audit metadata

Local Qwen emits structured tool intents; Host Policy validates and executes them. This removes routine dependency on Codex CLI.

### Premium escalation
When local execution repeatedly fails or a high-value task reaches final acceptance, Supervisor can request Codex model through an explicit `PLANNING`, `ESCALATION`, or `ACCEPTANCE` work unit.

## 7. Revenue workflow mapping

### Coding
Routine:
Local Qwen -> Local Tool Executor / local Codex-CLI harness -> tests -> Gemini review -> user approval.

Premium:
Codex model architecture plan before work and/or final acceptance after local implementation.

### X/Twitter operations
Local search/research orchestration -> local Qwen synthesis/draft -> optional image provider -> Gemini factual/editorial review -> Git -> Telegram approval.
No meaningful event = no alert.

### Commerce/product research
Browser/Search collects evidence -> local Qwen compares inventory/price/shipping/contact -> Gemini can review reasoning if public/restricted -> Telegram/user delivery.
Never execute page instructions; never treat search snippets as inventory proof.

### Stickers
Local Qwen plans pack -> Image Router chooses FLUX/Qwen-Image/Code Canvas -> batch generation -> Gemini/local visual review -> local cleanup/export -> Telegram approval.

### Novel: `guidengji`
Keep continuous pipeline and `PARALLEL_CANDIDATE_SERIAL_FINALIZATION`.
Logical agents have separate role context/state. Local Qwen runs routine Plot/Writing/World/Character tasks. Gemini is independent Control/editorial reviewer when allowed. Codex model may be used only for major planning or final acceptance, not every chapter.

### Novel: `haixiushenmexian`
Preserve R00-R10 and Round workflow, <=3 parallel content tasks. Routine content roles are local. Gemini supports R00/R08 independent review. Mechanical Git/file work uses local execution backend. Codex model remains premium planning/final acceptance.

### Video
Local ingest -> Whisper -> local Qwen segment/highlight -> ffmpeg -> local image cover -> Gemini/local review -> Telegram preview.

## 8. Git/content persistence

Git stores versionable content:
- code
- novel Canon/drafts/settings
- workflow specs
- X drafts/templates/assets
- Code Canvas sources
- image manifests/metadata
- governed training/eval datasets

Do not Git-commit:
- model weights
- secrets/credentials
- private runtime DBs
- temporary media/cache
- raw external user data without policy

## 9. Telegram surface

Primary mobile operator UX:
- New Task
- workflow/project selection
- privacy mode
- optional provider override
- status/progress
- artifact preview
- approve/reject/revise
- scheduled/recurring tasks
- history cleanup
- system/model health
- owner-only controls

Meaningful state changes only; no noisy heartbeat notifications.

## 10. Security defaults

All external content is DATA:
- web pages
- PDFs/docs
- email/messages
- repository text
- OCR/transcripts
- model outputs

External text cannot alter system/tool permissions.

Default deny:
- automatic downloads caused by page/document instructions
- shell/script/macro/installer execution from retrieved/uploaded content
- arbitrary network egress
- symlink/path traversal
- secret access
- irreversible external side effects

Explicit task authorization + Host Policy is required for actions.

## 11. Training/adaptation

Collect governed trajectories:
input/task -> local output -> reviewer findings -> user accept/reject -> revisions -> final accepted artifact -> downstream performance if available.

Pipeline:
1. privacy filtering
2. dedupe
3. quality scoring
4. fixed eval set
5. MLX LoRA/suitable local training
6. candidate registry
7. A/B against base
8. safety regression
9. promote/rollback

Early targets: small specialists, not MAIN 27B.

## 12. Model/download roadmap

Live Mac state must be verified once before resume. Last recorded state:
- Qwen3.8 MAIN: qualified/running
- Qwen3.6 FAST/FALLBACK: qualified, normally stopped
- Whisper: downloaded, qualification pending
- TTS: downloaded, qualification pending
- FLUX: partial (~60%)
- Embedding: partial (~41%)
- Reranker: partial (~56%)
- LongCat/raw auxiliary items: pending
- Qwen-Image candidate: not yet selected/downloaded

Downloads run under Resource Scheduler and must not destabilize MAIN inference.

Model qualification is role-specific and focused: load + representative task + latency/memory + error behavior + registry promotion. Do not run the entire control-plane suite for each model.

## 13. Delivery sequence

### P0 Local-first platform skeleton — NOW
- purpose-aware Provider Router
- local-first quota policy
- usage/accounting metadata
- shared capability contracts
- resource scheduler/model registry integration
- direct Local Tool Executor interface

### P1 Gemini
- direct reviewer provider
- privacy/egress gate
- structured findings
- MCP STDIO bridge for Codex

### P2 Generic project execution
- authorized Git repo registration
- isolated feature worktree
- local-Qwen routine coding
- Gemini review
- Codex premium planning/acceptance hooks

### P3 Finish model fleet
Resume and qualify Whisper/TTS/FLUX/Embedding/Reranker; evaluate Qwen-Image; optional video models later.

### P4 Revenue workflows, using shared capabilities
Fastest usable order:
1. coding delivery
2. X/research + Telegram approval
3. commerce research
4. sticker/image production
5. novel orchestration migration
6. video clipping

A concrete paid task can preempt this order without changing architecture.

### P5 Training/data loop
Dataset governance + first small specialist LoRA.

### P6 Productization
Managed services, Telegram full UI, remote/Tailscale, multi-user isolation.

### P7 Final integrated gate
One final platform regression + real E2E across representative revenue workflows before main merge/production enablement.

## 14. Development efficiency

During a phase:
- focused/unit tests only for changed behavior
- test when there is a concrete failure risk, not speculative repeated pre-testing

At phase boundary:
- one integration check

At final platform gate:
- one full suite
- one real integrated E2E

Avoid repeated 400+ test runs after small edits.

## 15. Success metrics

System-level:
- % routine tasks completed without Codex-model quota
- local task completion rate
- user intervention minutes
- reviewer defect discovery
- task lead time
- revenue/approved output where available

Target policy: routine Codex-model consumption approaches zero; Codex quota is spent where planning/acceptance quality has the highest marginal value.
