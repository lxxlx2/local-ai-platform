# Current Production Status / Handoff

Status type: **CANONICAL CURRENT STATE**

Last verified: `2026-08-24T19:59:40+07:00` (`Asia/Bangkok`)

Runtime code baseline: `ea9bd4fa042b63d47685daef0cd74433d96fceda`

The runtime code baseline is the last functional-code baseline. When reading
this file, still run `git rev-parse origin/main` to obtain the latest docs-only
HEAD.

## 1. TL;DR

- The private Telegram Bot is healthy and uses long polling; no webhook is set.
- Production MAIN/VISION is `mlx-community/Qwen3.8-27B-8bit`.
- Qwen3.8 is managed, resident on localhost port 8001, identity-verified, and
  qualified through 16K context. A real 24K run hit Metal OOM, so 32K was
  correctly skipped after that resource limit.
- `mlx-community/Qwen3.6-35B-A3B-4bit` is the downloaded, validated, on-demand
  FAST/FALLBACK model. It is normally not resident; port 8000 is empty.
- Real-Mac MAIN → FAST → MAIN and cold FAST → MAIN lifecycle validation passed.
- Auxiliary downloads are intentionally PAUSED with zero workers and no
  quarantine blocker. Partial data is preserved.
- Whisper Large V3 and both selected Qwen3-TTS snapshots are downloaded but not
  yet production-qualified.
- Stage 5A source/config safety audit passed, but the controlled Supervisor
  start failed closed during exact start-identity capture.
- Attempted Supervisor PID `56843` is dead, its lease expired, and no old job,
  execution, review, or mutation fence exists.
- Supervisor remains STOPPED; Real Codex and any real Producer remain disabled.
- Next: fix and independently review the Supervisor process-identity startup
  incompatibility, then rerun Stage 5A before Stage 5B.
- Gemini integration remains an unimplemented roadmap item in Issue #1.

## 2. Source-of-Truth Rules

- Live runtime evidence overrides this snapshot for current PIDs and resources.
- Git `main` overrides old conversation notes.
- This file is the latest canonical handoff.
- Historical review documents retain their historical meaning.
- Downloaded does not mean qualified; qualified does not mean deployed/resident.

## 3. Current Git State

- Repository: `lxxlx2/local-ai-platform` (PRIVATE).
- Local path: `/Users/jerson/AI`; production branch: `main`.
- Runtime code baseline: `ea9bd4fa042b63d47685daef0cd74433d96fceda`.
- At task start local `main` and `origin/main` matched that baseline and the
  worktree was clean.
- Expected steady state is clean. Models, secrets, caches, runtime databases,
  logs, downloads, and `.incomplete` files remain outside Git.

## 4. Current Production Runtime

### Telegram Bot

- HEALTHY; one process, PID `26855` at verification time.
- Telegram `getMe`: PASS for `@Jersonliu_bot`.
- Long polling; webhook absent; pending updates 0; no webhook error.
- The Bot was not restarted by Stage 5A.

### Qwen3.8 MAIN / VISION

- Model/path: `mlx-community/Qwen3.8-27B-8bit` at
  `/Users/jerson/AI/models/qwen38-27b-8bit`.
- Resident, managed, healthy, identity `MATCH`; verification PID `38697`.
- One listener on `127.0.0.1:8001`; `/health` returned HTTP 200/healthy.
- Production MAIN and VISION; max qualified context 16,384 tokens.

### Qwen3.6 FAST / FALLBACK

- Model/path: `mlx-community/Qwen3.6-35B-A3B-4bit` at
  `/Users/jerson/AI/models/mlx-community/Qwen3.6-35B-A3B-4bit`.
- On-demand FAST/FALLBACK through oMLX; stopped, identity `DEAD`, port 8000 empty.
- Post-setproctitle real-Mac form: executable `/Users/jerson/AI/omlx-server`,
  argv `("omlx-server",)`.
- True Codex-style autonomous structured tool looping is not qualified; an
  external Codex Producer remains responsible for coding-agent work.

### Supervisor

- Actual state: STOPPED / STAGE 5A BLOCKED.
- Existing entrypoint: `control-plane/scripts/start-supervisor.sh` →
  `python -m local_ai_control.supervisor.app daemon`.
- The controlled start returned `ORPHAN_RECONCILIATION_REQUIRED` during exact
  start-identity capture. PID `56843` is confirmed dead; no signal or arbitrary
  process control was used. Its lease expired at
  `2026-08-24T19:59:10.571506+07:00`.
- Durable state: queue 0, running 0, executions 0, review results 0, active
  mutation fences 0, unresolved reconciliation 0.
- Real Codex/Producer are disabled; auto merge/deploy do not exist in V0.1.

### Downloads, Ports, Security

- Downloads: PAUSED; stored manager identity `DEAD`, active 0, quarantine 0.
- Port 8000 EMPTY; port 8001 has one exact managed Qwen3.8 listener.
- Localhost-only model services; no new public port, webhook, or Funnel.
- Supervisor control is Owner-authorized before lookup/mutation. Public has no
  Supervisor navigation; forged callbacks pass through the Owner gate.
- Supervisor runtime is 0700; DB/WAL/SHM are 0600. Runtime/secrets/models/logs
  are ignored by Git.

## 5. Stage Completion Matrix

| Stage / gate | State | Commit or evidence | Notes |
|---|---|---|---|
| Stage 1 Bot deployment | PASS | `29aaea5`, PID 26855, Telegram API | Private long-polling Bot deployed. |
| Stage 2 acceptance/readiness | PASS | `6c43ec1`, production acceptance history | Telegram UX/security gates completed. |
| Stage 3 managed Qwen3.8 adoption | PASS | `04d8098`, port 8001 identity MATCH | Qwen3.8 is MAIN/VISION. |
| Stage 4 MAIN/FAST lifecycle | PASS | `ea9bd4f`, real-Mac evidence | MAIN → FAST → MAIN and cold FAST → MAIN passed. |
| oMLX identity compatibility | PASS | `27e47a0`, real-Mac validation | Exact saved identity remains required. |
| Heavy transition preflight | PASS | `ea9bd4f` | Preflight follows exact-owned resident reclaim. |
| Stage 5A disabled Supervisor start | BLOCKED | failed PID 56843 | Exact start identity not captured; daemon stopped. |
| Stage 5B recovery acceptance | PENDING | requires Stage 5A PASS | Do not begin yet. |
| Real Codex activation | PENDING | `REAL_CODEX_EXECUTION_REVIEW_PENDING` | Explicit review/authorization required. |
| Gemini MCP bridge | PENDING | GitHub Issue #1 | Roadmap only. |

## 6. Model Capability Matrix

| Model | Download | Qualification | Role / resident | Context or modality | Limitation |
|---|---|---|---|---|---|
| `mlx-community/Qwen3.8-27B-8bit` | COMPLETE (30,499,409,920 `du` bytes) | MAIN + VISION PASS | MAIN/VISION; YES | 16K; vision PASS | 24K Metal OOM; 32K skipped. |
| `mlx-community/Qwen3.6-35B-A3B-4bit` | COMPLETE (20,451,217,408 `du` bytes) | VALIDATED | FAST/FALLBACK; NO | 8K/32K capable; 64K special evidence | Autonomous Codex loop not qualified. |
| `mlx-community/whisper-large-v3-mlx` | COMPLETE; payload 3,083,522,487 bytes | NOT STARTED | future STT_MAIN; NO | transcription | Runtime qualification required. |
| `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16` | COMPLETE; 4,544,212,739 bytes | NOT STARTED | future TTS_MAIN; NO | speech synthesis | MLX Audio qualification required. |
| `mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16` | COMPLETE; 4,520,194,992 bytes | NOT STARTED | future TTS_DESIGN; NO | voice design | Owner policy/runtime qualification required. |
| `mlx-community/FLUX.2-klein-4B-bf16` | PARTIAL 60.09% | NOT STARTED | future IMAGE_MAIN; NO | image generation | Download and adapter qualification incomplete. |
| `Qwen/Qwen3-Embedding-8B` | PARTIAL 40.66% | NOT STARTED | future EMBED; NO | embeddings/RAG | Download/Apple Silicon qualification incomplete. |
| `Qwen/Qwen3-Reranker-8B` | PARTIAL 55.65% | NOT STARTED | future RERANK; NO | reranking/RAG | Download/Apple Silicon qualification incomplete. |
| `mlx-community/LongCat-Video-q8` | PENDING 0% | NOT STARTED | future VIDEO_HIGH; NO | video generation | Strict 48 GB machine gate. |
| `orcarouter/Qwen3.8-27B-Uncensored-MLX` (`8-bit/*`) | PENDING 0% | NOT STARTED | future RAW Owner-only; NO | raw candidate | Full qualification/security boundary required. |

## 7. Download Queue Snapshot

Verified at `2026-08-24T19:57:00+07:00`:

- Manager PAUSED; stored PID 46236 not live/verified; active 0, quarantine 0.
- Expected: `130,583,856,831` bytes (121.616 GiB).
- Verified completed payload credited: `12,147,930,218` bytes (11.314 GiB).
- Non-complete payload present: `1,802,705,808` bytes (1.679 GiB).
- Non-complete `.incomplete` cache: `27,745,320,960` bytes (25.840 GiB).
- Credited present: `41,695,956,986` bytes (38.832 GiB).
- Remaining: `88,887,899,845` bytes (82.783 GiB); progress 31.93%.

Completed items are capped at configured expected bytes. Payload plus resumable
`.incomplete` bytes count for non-complete items. The completed TTS Base
73,400,320-byte residual cache is reported but not credited as extra progress.
See `docs/MODEL_DOWNLOAD_QUEUE.md` for per-item bytes.

## 8. Supervisor State

- Stage 5A: BLOCKED; Supervisor STOPPED.
- Runtime: `/Users/jerson/AI/runtime/supervisor` (0700).
- DB: `/Users/jerson/AI/runtime/supervisor/supervisor.db` (0600), SQLite 3.53.4;
  migrations are table/column based and `user_version=0`.
- Log: `runtime/supervisor/supervisor.log` (0600, rotating 1 MB × 3 backups).
- Counts: jobs/queued/running/approvals/retries/recoveries/failed/stale/executions/
  reviews/fences all 0. Active lease/lock count is 0 after TTL expiry.
- Last heartbeat: `2026-08-24T19:56:10.571506+07:00`; last clean shutdown:
  NOT RECORDED.
- Real Codex DISABLED; real Producer DISABLED; no silent real fallback.
- Functional probe NOT RUN because the daemon failed before identity capture.
- Stale historical wording remains in `docs/WORKFLOW_SUPERVISOR.md`,
  `docs/ARCHITECTURE.md`, and parts of `docs/ROADMAP.md`; this file is current.
- Next: repair macOS/venv exact process-signature capture, add regression,
  independent review, then repeat the controlled disabled-mode start.

## 9. Gemini / Google AI Roadmap

- [Issue #1 — Codex ↔ Gemini MCP Bridge 独立 Reviewer MVP](https://github.com/lxxlx2/local-ai-platform/issues/1)
  is OPEN and says ROADMAP / HOLD; implementation is NOT STARTED.
- Codex is primary Producer; Gemini is an untrusted external secondary
  expert/reviewer/multimodal provider; Qwen Local is private/local MAIN/FAST.
- Gemini cannot own repository writes, Git, shell, tests, merge, deployment, or
  final technical decisions.
- Proposed standalone `gemini-codex-bridge/`: MCP STDIO, official MCP Python SDK,
  Google GenAI SDK; tools `ask`, `review_code`, `review_architecture`,
  `multimodal`.
- Private-repo egress requires explicit policy, secret/PII scanning,
  minimization and manifests. Free Tier is not safe for confidential whole-repo
  upload.
- Build/evaluate the MVP after Supervisor disabled-mode acceptance and before or
  alongside Real Codex activation. Stage 5A adds no Gemini code.

## 10. Open Risks / Known Gaps

- Supervisor exact start-identity capture fails on this Mac; Stage 5A is blocked.
- Rollback preflight is reviewed/tested, but no intentional real-Mac
  partial-start fault injection has been performed.
- Real Codex execution remains disabled/unreviewed for activation.
- Auxiliary models still need qualification after download.
- Embedding/Reranker/RAG, FLUX, LongCat, and RAW are not production complete.
- LongCat remains unqualified for this 48 GB machine.
- Gemini Bridge is not implemented; privacy/egress gates remain design work.
- The 6 GiB swap ceiling remains mandatory before every new heavy start.

## 11. Next Recommended Sequence

```text
Fix + independently review Supervisor process-identity startup blocker
  → repeat Stage 5A controlled start
  → Stage 5B disabled-mode acceptance/recovery
  → Gemini MCP Bridge MVP
  → Real Codex review/activation
  → Codex + Gemini + Qwen router
  → resume auxiliary downloads
  → Embedding/Reranker/RAG
  → FLUX image
  → LongCat video
  → RAW owner-only model
```

## 12. Hard Safety Constraints

- Exactly one heavy runtime; never control an unknown listener.
- No arbitrary PID kill or broad process matching; use exact ownership.
- Secrets/runtime/models/cache/`.incomplete`/private data never enter Git.
- Public cannot own/admin private Supervisor workflows.
- Human approval precedes real executor activation, merge, and deploy.
- Gemini cannot directly own Git, repository writes, shell, or deployment.
- Model download is not qualification; qualification is not deployment.

## 13. New-Conversation Bootstrap

Read `README.md`, this file, `docs/MODEL_DOWNLOAD_QUEUE.md`,
`config/model-download-queue-v0.1.json`, and GitHub Issue #1. Then verify
`git rev-parse origin/main`, live runtime, downloads, and Supervisor state.
Never assume old PIDs remain current, paused downloads run, Real Codex is
enabled, or the Gemini Bridge exists.

## 14. Machine-Readable Summary

```yaml
status_schema: "1"
runtime_code_baseline: "ea9bd4fa042b63d47685daef0cd74433d96fceda"
stage4: PASS
stage5a: BLOCKED
bot: HEALTHY
main_model: mlx-community/Qwen3.8-27B-8bit
main_role: MAIN
fast_model: mlx-community/Qwen3.6-35B-A3B-4bit
fast_role: FAST_FALLBACK
qwen36_resident: false
supervisor: STOPPED_START_IDENTITY_BLOCKED
real_codex: DISABLED
gemini_bridge: NOT_IMPLEMENTED
downloads: PAUSED
next_stage: FIX_SUPERVISOR_START_IDENTITY_THEN_REPEAT_STAGE5A
```
