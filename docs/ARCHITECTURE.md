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

## Interactive coding UI and automatic provider failover

For Owner-present coding, Codex Desktop is the primary interactive GUI. OpenAI Codex and Local Qwen3.8 are providers behind one durable coding-task contract; a provider change must not create a new logical task.

The target interactive path is:

```text
Owner in Codex Desktop
        |
        v
Durable coding task / feature worktree
        |
        v
Provider failover controller
        |
   +----+-----------------------+
   |                            |
Codex available             Codex exhausted/unavailable
   |                            |
   v                            v
OpenAI Codex              Local Qwen3.8 MAIN
                              |
                              v
                     Codex-Qwen UI adapter
                              |
                              v
                     same project/worktree
```

Automatic local takeover is triggered only by deterministic evidence such as explicit quota exhaustion, a recognized quota/rate-limit failure on the active Codex request, or cloud-provider unavailability combined with a healthy attested Local Qwen route. Account-wide `usedPercent` changes alone are diagnostic and must not be treated as per-task execution attribution.

The durable handoff preserves at least objective, worktree, branch, workflow stage, provider history, diff identity, completed tests, unresolved findings, review state, handoff state, and approval state. A same-chat-thread hot swap is not a hard requirement because the Codex client may not support it reliably; the hard requirement is the same GUI, project/worktree, durable task, and automatic progress continuation.

The local interactive adapter may reuse the existing Responses-compatible `codex_qwen_bridge` and isolated Codex provider/profile work. It must not silently overwrite the Owner's normal cloud Codex configuration. See `CODEX_AUTO_FAILOVER_ARCHITECTURE.md`.

## Unattended local implementation executor

Direct Local Qwen remains canonical for unattended/background coding and must not depend on Codex Desktop being open:

```text
Telegram / scheduler / 7x24 task
  → Supervisor
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

The production Direct Qwen provider is authorized by deterministic route attestation. Its endpoint must be exactly `http://127.0.0.1:8001`; a different or missing provider route is blocked before generation.

Local Qwen therefore has two front ends with different interaction roles:

```text
Local Qwen3.8
  ├─ Codex Desktop adapter: Owner-present interactive coding
  └─ Direct Local Qwen Agent: unattended/background coding
```

Both paths must preserve the same durable task/worktree identity and security policy where applicable. Neither path grants Qwen commit, push, merge, deploy, credential, arbitrary-network, service-control, or unrestricted filesystem authority.

## Codex quota and recovery policy

OpenAI Codex quota is reserved for high-value planning, escalation, and acceptance/review, while routine implementation should preferentially remain local. The interactive failover controller exists so quota exhaustion never forces a durable coding job to wait for the next quota window when Local Qwen is healthy and qualified for the work.

Quota recovery does not interrupt a mutating Local Qwen step. At the next safe workflow boundary, routine implementation may stay on Local Qwen; planning, escalation, or acceptance may route back to OpenAI Codex according to policy. Every transition is appended to provider-history evidence.

Codex Desktop remains a human-facing GUI, not a required 7x24 daemon. Background platform work must continue when the Desktop app is fully quit.

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
