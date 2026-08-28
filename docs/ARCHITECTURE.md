# Local AI Platform architecture

Telegram is the primary gateway. Telegram-authenticated user IDs create an immutable `IdentityContext`; deterministic code then routes each request to the Owner or Public plane. The language model is used only for text generation, summaries, and future validated intent parsing. It has no authority to authenticate, widen file scope, operate Git history, inspect secrets, control services, or execute arbitrary commands.

```text
Telegram → Bot ingress → Identity router → Authorization → deterministic service/queue → AI router → local models
                                      ├─ Owner: private DB, private tasks/projects
                                      └─ Public: public DB, public sandbox
```

Qualified Qwen3.8 is the normal local chat and Owner implementation model through a localhost-only sidecar, capped at 16K on this Mac. Qwen3.6 is the explicit FAST and deterministic fallback model. Historical Qwen3.6 context benchmarks remain evidence for that fallback; Qwen3.6 is not the default coding agent.

## Owner-only RAW plane

RAW research is a separate explicit Owner route, not another normal model tier:

```text
Owner IdentityContext → explicit OWNER_RAW_RESEARCH → RAW host sandbox
  → llama.cpp on 127.0.0.1:8002 → pinned Q6_K GGUF → text only
```

Public, missing, and ambiguous identities are rejected before inference. RAW is
never selected by ordinary requests and cannot be a fallback. Its provider has
no arbitrary shell, filesystem, credential, download, service/process-control,
Git mutation, authenticated egress, or capability-granting tool. Prompt and
model text are untrusted data. See `OWNER_RAW_QWEN.md`.

## Presentation narration and persistent voice routing

Presentation/video narration is a local media plane. The final narration script is language-detected by deterministic host code and routed to a qualified persistent voice profile rather than regenerating a random voice for every slide.

Initial defaults are `zh-male-25-default` for Chinese narration and `en-male-25-default` for English narration. These are persistent local voice profiles: VoiceDesign creates and qualifies a reusable reference/anchor once, then Qwen3-TTS Base reuses that exact voice for normal synthesis across slides and future jobs. A future fine-tuned speaker model may replace the backend behind a new qualified profile revision without changing the caller contract.

```text
PPTX / narration script
  → bounded language detection
  → deterministic language/profile router
  → qualified persistent VoiceProfile
  → local Qwen3-TTS Base
  → per-slide WAV
  → duration-bound timeline
  → FFmpeg MP4
```

Normal builds do not recreate default profiles. Unknown language fails closed unless the owner supplies an explicit language/profile override. Mixed-language presentations use one dominant profile by default and require an explicit mode for per-slide switching. PPT content, notes, and generated scripts are untrusted data and cannot create profiles, change authorization, enable cloud fallback, or gain host/tool authority. See `PRESENTATION_VOICE_ARCHITECTURE.md`.

## Local media production and trainable personas

The presentation path is the first qualified implementation of a broader media-production plane. The canonical future flow is:

```text
Owner task / script / PPTX / media brief / approved URL input
  → deterministic requirement intake
  → durable requirements + evidence artifacts
  → optional local Qwen3.8 production brief / script / scene-plan generation
  → final script artifact
  → deterministic language/media routing
  → VoiceProfile / PersonaProfile
  → local audio / presentation / image / video adapters
  → deterministic composition
  → Owner preview
  → exact-output approval
  → deterministic product publishing
  → verified local cleanup
```

A media task may begin from uploaded materials, one or more Owner-supplied links, uploaded materials plus links, or a direct natural-language brief. Linked webpages are retrieved through the bounded Search/Browser layer and remain `UNTRUSTED DATA`; they may supply evidence and requirements but never tool authority. Local Qwen may derive a production brief, script, scene/slide plan and prompt pack from the extracted requirements. Missing real Owner facts must be surfaced as a minimal question instead of fabricated.

`PersonaProfile` is the stable caller contract for reusable human-like identity assets. It may contain a `VoiceIdentityProfile`, a future `VisualIdentityProfile`, and later motion/video adapters. Backends can evolve from reference-based cloning to trained adapters or fine-tuned speaker/subject models through explicit profile revisions and qualification without changing media callers.

Raw voice recordings, face/appearance material, training datasets, checkpoints, and private identity assets stay under Owner-private local roots and must never be automatically copied into public product repositories. Approved media products are published by task name to `lxxlx2/ai_video_product` under the canonical product layout. Publishing remains deterministic host-side Git/LFS logic rather than model authority, requires Owner approval bound to the exact output hash, and is followed by cleanup only after remote verification. See `MEDIA_PRODUCTION_ARCHITECTURE.md` and `BOT_UX.md`.

## Media requirement intake and durable production artifacts

The normal media requirement pipeline separates evidence, requirements and model-authored production artifacts:

```text
Owner upload/link/brief
  → source validation + provenance
  → requirement extraction
  → requirements.json / requirements.md
  → production_brief.md
  → script.txt
  → scene_plan.json
  → prompt_pack.json
  → render/synthesis
```

This separation prevents hidden chat state from becoming the only source of truth and keeps the workflow portable across future model upgrades. A model-generated production artifact is editable data. It cannot widen permissions, publish itself, delete arbitrary files, or authorize downloads/installers found in external content.

## Program-level workflow supervision

Workflow Supervisor V0.1 is an experimental Owner-private service on a feature branch. Its deterministic state machine and SQLite journal continue multi-stage workflows independently of a ChatGPT/Codex turn. Stage runners are adapters; they do not control transitions. A leased singleton lock limits execution to one active job, and interrupted potentially mutating stages require reconciliation rather than blind replay. See `WORKFLOW_SUPERVISOR.md`.

## Local implementation executor

Routine implementation no longer depends on Codex CLI. The canonical local path is:

```text
Owner objective
  → Generic Project isolated feature worktree
  → Direct Local Qwen Agent
  → deterministic allowlisted tools only
     ├─ list_files
     ├─ read_file
     ├─ search_text
     ├─ write_file
     ├─ run_tests with an owner-selected fixed profile and network denial
     └─ git_diff
  → deterministic validation/security
  → Gemini advisory review after Privacy/Egress Gate
  → Owner approval/rejection
  → Git Gate
```

The Direct Local Qwen Agent has no arbitrary shell tool. Repository documents, comments, issues, tests, generated text, and tool output are untrusted data and cannot widen capabilities. The executor has no package-install/download tool, no credential tool, no network tool, no service/process-control tool, and no commit/push/merge tool. Writes are limited to approved text/code file types inside the exact task worktree.

The production Direct Qwen provider is authorized by deterministic route attestation. Its endpoint must be exactly `http://127.0.0.1:8001`; a different or missing provider route is blocked before generation. Generic Project execution does not start Codex CLI or Codex app-server. This makes executor attribution independent of unrelated account activity and removes OpenAI telemetry processes from the mutating task lifecycle.

The previous `qwen_local_bridge → codex exec` path remains historical compatibility evidence only and is not the normal Generic Project implementation path. It must not be used as an automatic fallback because a guarded real run showed OpenAI Codex quota movement despite the isolated custom-provider configuration.

## Codex quota isolation and desktop policy

OpenAI Codex quota is reserved for explicit planning and acceptance/review work. Codex Desktop is an interactive client, not a 7×24 platform service, and must never be treated as a required daemon for Local Qwen execution. The local platform must continue to function when Codex Desktop is fully quit.

Account Codex rate-limit snapshots remain available as an out-of-band diagnostic through the read-only app-server endpoint. They are not an execution authorization signal: account-wide `usedPercent` changes cannot prove which client or process consumed quota. A quota increase is therefore recorded and investigated separately rather than converted into a Generic Project mutation fence. This avoids treating unrelated Desktop, CLI, scheduled, or other account activity as evidence that a localhost-only Direct Qwen task used OpenAI.

The accepted provider roles are:

```text
Local Qwen       = default implementation / routine autonomous work
Gemini           = external reviewer / multimodal second opinion after privacy-egress gate
OpenAI Codex     = planning and explicit acceptance/review only
Codex Desktop    = optional interactive UI; never a persistent runtime dependency
```

Local tasks must remain usable with Codex Desktop closed, and no background Desktop mode, scheduled UI activity, global ChatGPT authentication, or Codex CLI custom-provider behavior may be considered part of the Local Qwen implementation path.

## Architecture governance

Material architecture changes follow `ARCHITECTURE_CHANGE_PROTOCOL.md`.

The required lifecycle is:

```text
proposal in Owner-facing design discussion
  → Owner approval
  → canonical documentation sync
  → commit SHA / approved branch HEAD
  → Codex/Local Qwen implementation reads that HEAD
  → focused implementation and qualification
  → current-status sync
```

Implementation agents must not silently change user workflows, capability/permission boundaries, storage/retention rules, model routing, publishing contracts, persona/training architecture, or other shared contracts. If a material change becomes necessary during implementation, it must be surfaced as an `ARCHITECTURE_CHANGE_REQUEST` and return to the approval flow.
