# Local-First Product and Model Plan

Status: USER-APPROVED ACTIVE MASTER EXECUTION PLAN
Approved baseline: 2026-08-25
Branch: `feat/local-qwen-owner-raw-v04`

This document is the highest-level execution source of truth for current platform development. Related issues #14, #15, #16 and #17 provide infrastructure, product, novel and revenue detail. If an older issue/body conflicts with this document, this document wins until the issue is synchronized.

## 1. Product objective

Build a local-first AI production workstation whose routine revenue work runs on local models and deterministic local tools. Cloud/model quota is used only where it adds disproportionate value.

The platform is intended to make money and save operator time, not to demonstrate models.

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

Every platform feature should map to revenue, quality, operator time saved, safety, or future model portability.

## 2. Non-negotiable routing policy

### 2.1 Routine execution is local-first

Default worker: LOCAL.

Routine production must not consume OpenAI Codex model quota when a qualified local capability exists.

Examples:
- code implementation/debug/test/revision loops
- novel planning/drafting/continuity/role-agent work
- X research synthesis/drafting
- commerce research synthesis
- sticker/image orchestration
- video transcription/highlight/edit planning
- RAG/retrieval
- recurring monitoring/task processing
- web research synthesis

Target metric: routine Codex-model quota consumption approaches zero.

### 2.2 OpenAI Codex model quota

Reserve Codex model quota for:
1. high-value architecture/planning;
2. difficult diagnosis/escalation after local attempts fail;
3. important final acceptance/verification.

Codex is not the default production worker.

A Codex CLI process configured to use the local Qwen provider is a local execution harness and must not be counted as Codex-model quota usage. The platform must record which actual provider/model generated each turn.

Long term, routine execution should use a direct `LocalToolExecutor` so ordinary local workflows do not depend on Codex CLI at all.

### 2.3 Gemini is a cloud reviewer, not a local model

Gemini integration uses the official Google Gemini Developer API. It is not a downloaded/local Gemini model.

Initial production policy:
- API tier: FREE by default
- billing: not required by default
- default model: `gemini-3.7-flash`
- SDK: official `google-genai`
- role: independent cloud review / second opinion / multimodal review
- no shell/Git/file-write/deploy/service authority

Current Google API facts to account for in implementation:
- Gemini 3.7 Flash has a free Standard API tier for input/output tokens.
- Free-tier rate limits are project-scoped and may change; runtime must handle `429 RESOURCE_EXHAUSTED` and must not hard-code assumed quota numbers.
- Free-tier submitted content may be used by Google to improve its products.
- Paid tier can be added later through configuration, but the platform must never silently enable billing or paid usage.
- Gemini 3.x Search grounding should not be assumed available in the free Developer API path. Our own Search/Browser capability remains the primary web-retrieval layer; Gemini reviews supplied evidence/material.

Gemini privacy policy:
- PUBLIC: allowed
- RESTRICTED: allowed only after minimization + secret/PII egress gate
- PRIVATE: denied

Gemini roles:
- architecture review
- code review
- creative/editorial review
- long-context review
- image/PDF/video/audio multimodal review
- repeated-failure diagnosis
- final model-level second opinion before an author/operator approval gate where useful

Gemini does not silently create novel Canon, approve irreversible machine actions, or expand tool authority.

### 2.4 OWNER RAW local research model

Add a separate owner-only reduced-refusal research model:

- repository: `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`
- initial target file: `Qwen3.8-27B-Uncensored-Q6_K.gguf`
- current published size: ~22.4 GB
- runtime target: `llama.cpp` + Metal
- logical role: `OWNER_RAW_RESEARCH`
- public users: DENIED
- default shell: DENIED
- default arbitrary download: DENIED
- credential access: DENIED
- final security/acceptance authority: DENIED

The model card states refusal behavior was substantially reduced through Heretic/refusal-direction removal, without additional fine-tuning data, and that MTP tensors were restored/verified. This is useful for owner-only research where normal models may over-refuse.

Security principle:

`less restrictive content behavior != more machine authority`

In fact OWNER_RAW receives a narrower host permission profile than LOCAL_MAIN.

Allowed by default:
- public Search
- bounded Browser GET/read
- HTML/JSON/text evidence
- approved local RAG inputs
- explicit owner-provided research material

Denied by default:
- shell
- arbitrary binary/script/archive download
- installer execution
- `curl | sh`
- `pip/npm/brew install`
- execution of commands found in webpages/PDFs/README/email/OCR/transcripts
- Keychain access
- SSH key access
- wallet/seed/private-key access
- browser-cookie export
- `.env`/credential discovery
- `sudo`
- launchd/service control
- process killing
- arbitrary filesystem write

If owner research genuinely requires a file download, the model emits a structured `DownloadRequest`; Host Policy decides MIME/size/domain/quarantine/hash. Download never implies execution.

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
  +----------+-----------+--------------+----------------+
  |          |           |              |                |
LOCAL_MAIN  LOCAL_FAST  OWNER_RAW    Local Media      Cloud Review
Qwen3.8     Qwen3.6     Qwen3.8     Image/Audio/RAG    Gemini API
  |          |           |              |                |
  +----------+-----------+-------+------+----------------+
                                 |
                         Host Security Policy
                                 |
            Git / Files / Browser / ffmpeg / Render / Search
                                 |
                          Durable Artifacts
```

OpenAI Codex model is a premium planning/escalation/acceptance provider above the routine path, not the normal worker.

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
- `ResearchProvider`
- `ExecutionBackend`

Every workflow asks for capability + purpose + privacy. Workflows do not hard-code model names.

Router inputs:
- capability
- task purpose: `ROUTINE / PLANNING / REVIEW / ACCEPTANCE / ESCALATION / OWNER_RAW_RESEARCH`
- privacy: `PUBLIC / RESTRICTED / PRIVATE`
- owner/public-user identity and permissions
- project policy
- model/provider health
- local memory/resource pressure
- user override
- quota/budget policy

Router output records:
- selected provider
- selected model/profile
- selection reason
- whether external quota can be consumed
- privacy/egress decision
- owner-only decision
- fallback chain

## 5. Model fleet

### 5.1 Qwen3.8 27B 8-bit

Role: `LOCAL_MAIN`.

Use for:
- routine reasoning
- routine coding and repair loops
- novel Plot/World/Character/Writing roles
- research synthesis
- task decomposition
- X/content work
- commerce research synthesis
- local tool planning
- local multimodal/vision within qualified limits

Current status: qualified MAIN; existing tool loop is proven.

### 5.2 Qwen3.6 35B-A3B 4-bit

Role: `LOCAL_FAST_FALLBACK`.

Use for:
- fast/cheap local work
- fallback when MAIN unavailable
- classification/summarization
- possible local independent review
- future background/low-priority workloads

Current status: qualified, normally stopped.

### 5.3 Qwen3.8 Uncensored Q6_K

Role: `OWNER_RAW_RESEARCH`.

Purpose:
- owner-only sensitive/controversial research
- second research path for topics where normal assistants may over-refuse
- public-source evidence collection/synthesis under host restrictions

It must never become the default public-facing model or a privileged machine controller.

### 5.4 Future local specialists

Train/promote smaller models for:
- router/task classifier
- novel continuity/style review
- commerce evidence/ranking
- X content ranking
- code review
- prompt rewriting

Do not destabilize Qwen3.8 MAIN with premature fine-tuning.

### 5.5 Gemini Free API

Role: `CLOUD_REVIEWER_MULTIMODAL`.

Default model: `gemini-3.7-flash`.

Interfaces:
- `ReviewerProvider`
- `MultimodalProvider`
- optional `PlanningProvider` for explicit high-value use

Codex-facing integration: MCP STDIO tools such as `review_code`, `review_architecture`, `ask`, `multimodal`.
Supervisor-facing integration: direct provider interface using the same egress/privacy gate.

Free-tier exhaustion behavior:
- never automatically switch to paid billing
- on rate limit/unavailability, fall back to local independent reviewer where possible
- queue/retry only when policy permits and the task still benefits from cloud review
- surface important acceptance work if Gemini review could not be obtained

### 5.6 OpenAI Codex model

Role: `CLOUD_PREMIUM_PLANNER_ACCEPTOR`.

Allowed default purposes:
- PLANNING
- ACCEPTANCE
- ESCALATION after local/Gemini evidence justifies it

Routine purpose is denied unless the owner explicitly overrides the budget policy.

Track actual Codex-model quota usage separately from use of the Codex CLI executable.

## 6. Image stack

### FLUX
Role: general local generative image backend.
Use for general illustration, realistic imagery and broad creative generation.

### Qwen Code Canvas
Role: editable deterministic visual backend.

Flow:
`Qwen -> constrained p5.js/p5.brush -> AST/API policy -> sandboxed Chromium/Playwright -> PNG + editable source`

Generated drawing code gets:
- no network
- no ambient filesystem
- no `eval`
- no dynamic import
- bounded runtime/memory/output size

Best for:
- infographics
- icons
- posters
- stickers
- line art
- layouts requiring precise edits

### Qwen-Image / Qwen-Image-Edit
Role: text-heavy generation/editing candidate backend.
Evaluate a suitable Apple-Silicon/quantized variant before locking runtime.

### Visual review loop
`local image backend -> render -> Gemini Vision or local reviewer -> revise -> render -> Telegram/user approval`

Gemini is optional according to privacy/availability.

## 7. Sticker production for LINE / WeChat

Target: repeatable commercial pack production.

Pipeline:
1. market/theme/user brief
2. character/style bible + identity anchors
3. phrase/usage matrix
4. route to FLUX / Qwen-Image / Code Canvas
5. batch candidates
6. identity/text/pose/composition review
7. transparency/background cleanup
8. platform dimensions
9. duplicate/similarity screening
10. localized variants
11. package manifest + cover/tab images + metadata + editable sources
12. Telegram preview/approve/reject/revise
13. platform-ready export
14. external publishing remains explicitly authorized

Track regeneration rate, accepted images, operator time, platform rejection reasons and sales/performance when available.

## 8. Novel Workflow Engine

Existing novel repositories remain sources of truth and keep their own governance profiles.

### `guidengji`
Preserve:
- AUTHOR final creative authority
- Control/Canon AI
- Plot AI
- Writing AI
- Visual AI
- Visual execution role
- Git durable state
- `PARALLEL_CANDIDATE_SERIAL_FINALIZATION`
- parallel candidate work
- global Finalization slot max 1
- rolling prose Draft PR, default 10 chapters
- approval bound to exact candidate/head
- author short commands (`继续/通过/退回/重做/回滚/当前状态`)
- automatic machine-state recovery

Routine content roles run locally. Gemini provides independent editorial/continuity review when privacy permits. Codex model is reserved for major planning/final acceptance, not ordinary chapter production.

### `haixiushenmexian`
Preserve R00-R10:
- R00 editor/Canon/scheduling
- R01 world
- R02 character/ability
- R03 financial research
- R04 plot
- R05 visual
- R06 map/scenes
- R07 prose
- R08 review
- R09 governance/Git
- R10 image execution

Preserve Round workflow and <=3 parallel content tasks. Local models perform routine content work; Gemini supports independent R00/R08 review; local deterministic tools handle mechanical Git/file work; Codex model is premium planning/final acceptance only.

### Novel state migration

Never flatten novels into a prompt. Recovery priority:
1. Git main/CURRENT
2. active branches/PRs
3. Canon manifests/modules
4. author decisions
5. control/round/checkpoint Issues
6. approval commits/candidate state
7. handoffs
8. timeline/character/continuity state
9. visual assets/manifests
10. RAG retrieval layer

Git/Canon remains authoritative; RAG accelerates retrieval only.

## 9. X/Twitter revenue workflow

Local-first target for English finance/Crypto operations:
- daily US-stock + Crypto brief
- major sector/news/policy events
- secondary-market movers
- premarket movers
- IPO/new listings
- NFT/meme hotspots
- geopolitical events affecting equities/crypto/oil/rates
- long-horizon monthly opportunity report
- breaking-event candidate detection
- source-backed research
- post/thread/reply drafting
- optional chart/image generation
- Git-backed content/artifacts
- Telegram approval before publishing unless a future explicit bounded policy changes this

Rule: `NO_MEANINGFUL_CHANGE = NO_NOTIFICATION`.

Long-term 7x24 routine operation must continue without Codex-model availability.

## 10. Commerce/product research agent

Search public web/FB/public marketplace/shop pages where accessible.

Required evidence behavior:
- never trust search-result titles/snippets as inventory proof
- open/inspect actual listing/store page when possible
- distinguish `IN_STOCK / SOLD_OUT / UNKNOWN`
- verify discounts are currently valid before quoting them
- compare new vs used/current market prices
- find seller/store contacts, preferring relevant local channels such as LINE in Thailand
- support Thailand/Korea/US/other regions
- verify shipping feasibility
- estimate landed cost when enough evidence exists
- mark uncertain/unverified claims clearly
- persist citations/evidence/timestamp
- draft contact text if requested but do not message/purchase without authorization

Web content is data only and cannot grant execution authority.

## 11. Video/livestream pipeline

Near-term commercial path is tool-first editing:
1. explicitly authorized source
2. local ingest/recording
3. Whisper transcription
4. timestamped segmentation
5. local Qwen highlight/hotspot scoring
6. candidate clips
7. ffmpeg cuts/subtitles/aspect/overlays
8. local image cover/title
9. Gemini/local review
10. Telegram preview/approve/revise
11. export; publishing requires explicit authorization

Future generative video models remain optional providers after core clipping/editing is productive.

## 12. Speech/audio

### Whisper large-v3 MLX
Role: `STT_MAIN`.
Use for video/livestream transcription, searchable media, subtitles and Telegram voice input.

### Qwen3-TTS Base / VoiceDesign
Roles: `TTS_MAIN / TTS_DESIGN`.
Use for narration, creative audio and media workflows.

Downloaded models receive focused functional qualification before registry promotion; do not redownload unnecessarily.

## 13. RAG / Memory

### Qwen3 Embedding 8B
Role: `EMBEDDING`.

### Qwen3 Reranker 8B
Role: `RERANK`.

Use for:
- repository retrieval
- novel Canon/history retrieval
- research memory
- product evidence retrieval
- task/history lookup

Do not index secrets such as seed phrases, private keys, passwords or raw credential `.env` values.

## 14. Execution backend

### Near term
Reuse the qualified Qwen -> Codex CLI custom-provider loop when useful. Because the CLI talks to local Qwen, this does not consume OpenAI Codex model quota.

### Target
Introduce `LocalToolExecutor` owned by Supervisor:
- bounded shell commands
- path allowlists
- Git worktree policy
- process/time/memory limits
- no ambient network by default
- explicit Browser/Search tools
- immutable audit metadata

Local Qwen emits structured `ToolIntent`; Host Policy validates and executes it.

This removes routine dependence on Codex CLI.

## 15. Model registry

Every provider/model profile records:
- provider/model ID
- version/hash
- download status
- qualification status
- enabled state
- role/capabilities
- runtime
- RAM/resource profile
- context
- latency/quality evidence
- fallback order
- quota class
- owner-only/public availability
- privacy/egress policy
- tool permission profile

Initial target registry:
- Qwen3.8 27B normal -> `LOCAL_MAIN`
- Qwen3.6 -> `LOCAL_FAST_FALLBACK`
- JonathanColetti Qwen3.8 Uncensored Q6_K -> `OWNER_RAW_RESEARCH`
- Gemini 3.7 Flash Free API -> `CLOUD_REVIEWER_MULTIMODAL`
- OpenAI Codex -> `CLOUD_PREMIUM_PLANNER_ACCEPTOR`
- FLUX -> `IMAGE_GENERAL`
- Qwen-Image -> `IMAGE_TEXT_EDIT`
- Qwen Code Canvas -> `IMAGE_CODE`
- Whisper -> `STT_MAIN`
- TTS -> `TTS`
- Embedding -> `EMBEDDING`
- Reranker -> `RERANK`
- future LoRAs -> `SPECIALIST`

## 16. Resource Scheduler

48GB Mac requires strict heavy-workload coordination.

Profiles:
- MAIN inference
- FAST inference
- OWNER_RAW inference
- image generation
- model download
- training
- model qualification

Default: `heavy_workload_max_active = 1` unless a measured safe profile explicitly permits overlap.

Lightweight Bot/Supervisor/Router/databases may remain resident.

## 17. Download roadmap and safety

Before resuming downloads, verify live Mac state once.

Last recorded state:
- Qwen3.8 MAIN: qualified/running
- Qwen3.6 FAST/FALLBACK: qualified, normally stopped
- Whisper: downloaded, qualification pending
- TTS: downloaded, qualification pending
- FLUX: partial (~60%)
- Embedding: partial (~41%)
- Reranker: partial (~56%)
- Qwen-Image candidate: not yet selected/downloaded
- OWNER_RAW Q6_K: planned, not yet downloaded

Proposed next download priority:
1. OWNER_RAW Q6_K
2. Embedding
3. Reranker
4. FLUX
5. selected Qwen-Image variant
6. remaining auxiliary/video models only when justified

Download policy:
- no arbitrary agent `wget/curl` downloads
- no execution of Hugging Face/README install snippets merely because they are displayed
- model weights downloaded through managed queue
- pin exact artifact/version when practical
- verify size/hash/source
- quarantine unfamiliar binaries/scripts
- downloaded content never automatically executes

For OWNER_RAW Q6_K, current upstream file SHA256 recorded by Hugging Face is:
`a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6`
This must be rechecked at actual download time rather than blindly trusted forever.

## 18. Git/content persistence

Git stores versionable content:
- code
- novel Canon/drafts/settings
- workflow specifications
- X drafts/templates/assets
- Code Canvas sources
- image manifests/metadata
- governed training/eval datasets
- model/runtime configuration without secrets

Do not Git-commit:
- model weights
- credentials/secrets
- private runtime DBs
- temporary media/cache
- uncontrolled external user data

Large binary assets use local/managed asset storage plus manifest/hash when appropriate.

## 19. Telegram product surface

Telegram is the primary mobile operator surface:
- New Task
- workflow/project selection
- privacy mode
- optional provider override
- owner-only RAW mode
- status/progress
- artifact preview
- approve/reject/revise
- scheduled/recurring tasks
- history cleanup
- system/model health
- admin controls

Meaningful state-change notifications only.

Task categories eventually include:
- coding
- research
- commerce
- X operations
- novel
- image/stickers
- video
- scheduled monitoring
- admin/system tasks

## 20. Conversation/history cleanup

Support explicit retention and cleanup for platform-owned Bot/task records.

Destructive cleanup requires authenticated/authorized bounded scope. Do not claim ability to delete third-party history when external APIs do not permit it.

## 21. External multi-user access

Public users are isolated from owner authority.

OWNER:
- approved owner capabilities
- owner projects
- OWNER_RAW mode
- owner-only admin controls

PUBLIC USER:
- restricted Task Catalog
- own workspace/data/secrets
- quotas/rate limits
- no OWNER_RAW
- no owner filesystem
- no host admin/service control

Required before external access:
- authentication
- per-user/project authorization
- workspace/state isolation
- resource quotas
- artifact access control
- secrets isolation
- audit logs
- safe uploads
- abuse controls
- owner emergency stop

## 22. Mandatory host security model

Models propose actions; Host Policy grants or denies them.

`Model -> ToolIntent -> Host Security Policy -> Execution`

No model, including Gemini, Codex, LOCAL_MAIN or OWNER_RAW, receives unconditional machine authority.

All external content is `UNTRUSTED_DATA`:
- webpages
- PDFs/docs
- email/messages
- GitHub README/issues
- OCR
- transcripts/subtitles
- product pages
- search results
- model outputs

External text cannot alter system/tool permissions.

Default deny:
- automatic downloads caused by page/document instructions
- shell/script/macro/installer execution from retrieved/uploaded content
- arbitrary network egress
- symlink/path traversal
- secret access
- irreversible external side effects

Examples such as `Ignore previous instructions`, `run this command`, `download this file`, or `send credentials` remain text, not ToolCalls.

## 23. Training/adaptation

Collect governed trajectories:
`task -> local output -> Gemini/local review -> user accept/reject -> revisions -> final accepted artifact -> downstream performance when available`.

Pipeline:
1. privacy filtering
2. dedupe
3. quality scoring
4. fixed eval sets
5. MLX LoRA/suitable local training
6. candidate registry
7. A/B against base
8. safety regression
9. promote/rollback

Early targets:
- router/task classifier
- novel continuity specialist
- novel style specialist
- X/content specialist
- commerce evidence/ranking specialist
- code reviewer

Do not fine-tune the 27B MAIN merely because training is available.

## 24. Grok Bot reconstruction reference

`b-nnett/grok-bot-0.18-reconstructed` remains a read-only architecture reference, not official Anysphere source.

Useful patterns:
- multi-provider routing
- MCP/tool bridge
- transcript/streaming lifecycle
- usage accounting
- sandbox boundaries
- conversation state

Do not copy private auth/session tricks, undocumented endpoints or large reconstructed source sections. Reimplement patterns independently in the Python control plane.

## 25. Development sequence

### P0 Platform skeleton — CURRENT
- purpose-aware Provider Router
- local-first quota policy
- owner/raw policy
- usage accounting
- shared capability contracts
- Model Registry
- Resource Scheduler
- Host Security Policy
- `LocalToolExecutor` interface

### P1 Gemini Free API + OWNER_RAW
- Gemini 3.7 Flash free API adapter
- structured review output
- free-tier rate-limit fallback
- privacy/egress gate
- Codex MCP reviewer
- OWNER_RAW registry/runtime profile
- RAW research sandbox

### P2 Generic project execution
- authorized Git repo registration
- isolated feature worktree
- local-Qwen routine coding
- local execution backend
- Gemini review
- Codex premium planning/acceptance hooks

### P3 Research/revenue web layer
- Browser/Search capability
- Commerce Agent
- X research/operations
- citations/evidence store
- prompt-injection-resistant browsing

### P4 Finish/qualify model fleet
- OWNER_RAW Q6_K
- Embedding
- Reranker
- FLUX
- Qwen-Image evaluation
- Whisper/TTS focused qualification

### P5 Image/Sticker production
- Image Router
- FLUX
- Qwen Code Canvas
- Qwen-Image
- Gemini/local visual review
- LINE/WeChat Sticker Factory

### P6 Novel Workflow Engine
- migrate `guidengji`
- migrate `haixiushenmexian`
- durable multi-agent role state
- project-specific governance profiles

### P7 Video pipeline
- ingest
- Whisper
- highlight detection
- ffmpeg edit/export
- review/TG preview

Media Product Workflow V0.2 now provides the functional presentation-video
subset on the feature branch: durable evidence/requirements/script state,
brief or URL-driven local-Qwen scripts, deterministic no-PPT slides, qualified
persistent voice/TTS, MP4 composition, exact-output approval, fixed-target
Git/LFS publishing, verified cleanup, and a durable Owner Telegram wizard.
Whisper highlights and generative video remain separate qualification work and
are not implied by this status.

### P8 Training/data loop
- governed dataset capture
- evaluation
- first small specialist LoRA

### P9 Productization
- managed provider services
- full Telegram task UI
- remote/Tailscale
- retention controls

### P10 Multi-user/public service
- tenant isolation
- quotas
- restricted workflow catalog
- admin/emergency controls

### P11 Final integrated gate
One final platform regression + representative real E2E before main merge/production enablement.

A concrete paid task may preempt roadmap order without changing platform architecture.

## 26. Development efficiency

During a phase:
- focused/unit tests only for changed behavior
- no repeated full 400+ suite after small changes
- test concrete failure risk, not speculative loops

At phase boundary:
- one integration check

At final platform gate:
- one full suite
- one representative real integrated E2E

## 27. Success metrics

System-level:
- % routine tasks completed without Codex-model quota
- local completion rate
- operator intervention minutes
- reviewer defect discovery
- task lead time
- output acceptance rate
- revenue/approved output where available

Workflow metrics:
- coding first-pass acceptance/regressions
- sticker pack acceptance/regeneration/sales
- X useful posts/performance/noise rate
- commerce verified-hit rate and sourcing value
- novel chapters/week, revision rate, continuity defects and author time
- video useful clips/source-hour and approval rate

## 28. Final operator experience

The intended daily interface is Telegram/local UI, for commands such as:
- `查这个商品，核库存和最低价`
- `继续写归灯纪`
- `修这个 GitHub Issue`
- `做一套 LINE 表情包`
- `今天 X 有什么值得发`
- `把昨晚直播切成 5 个视频`
- `用 RAW 模式查这个资料`

Normal backend path:
`Router -> local model -> local tools -> Gemini review when privacy/quota permits -> Codex only for premium planning/acceptance/escalation -> Git/artifact persistence -> Telegram result/approval`.

For most routine tasks, OpenAI Codex-model quota should remain zero.
