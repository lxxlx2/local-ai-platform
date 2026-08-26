# Local AI Platform architecture

Telegram is the primary gateway. Telegram-authenticated user IDs create an immutable `IdentityContext`; deterministic code then routes each request to the Owner or Public plane. The language model is used only for text generation, summaries, and future validated intent parsing. It has no authority to authenticate, widen file scope, operate Git history, inspect secrets, control services, or execute arbitrary commands.

```text
Telegram → Bot ingress → Identity router → Authorization → deterministic service/queue → AI router → local models
                                      ├─ Owner: private DB, private tasks/projects
                                      └─ Public: public DB, public sandbox
```

Qualified Qwen3.8 is the normal local chat and Owner implementation model through a localhost-only sidecar, capped at 16K on this Mac. Qwen3.6 is the explicit FAST and deterministic fallback model. Historical Qwen3.6 context benchmarks remain evidence for that fallback; Qwen3.6 is not the default coding agent.

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
