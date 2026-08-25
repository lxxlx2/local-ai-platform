# Local AI Platform architecture

Telegram is the primary gateway. Telegram-authenticated user IDs create an immutable `IdentityContext`; deterministic code then routes each request to the Owner or Public plane. The language model is used only for text generation, summaries, and future validated intent parsing. It has no authority to authenticate, access files, operate Git, inspect system state, or execute commands.

```text
Telegram → Bot ingress → Identity router → Authorization → deterministic service/queue → AI router → oMLX
                                      ├─ Owner: private DB, private tasks/projects
                                      └─ Public: public DB, public sandbox
```

Qualified Qwen3.8 is the normal local chat model through a localhost-only sidecar, capped at 16K on this Mac. Qwen3.6 is the explicit FAST and deterministic fallback model. Historical Qwen3.6 context benchmarks remain evidence for that fallback; Qwen3.6 is not a coding-agent replacement.

## Program-level workflow supervision

Workflow Supervisor V0.1 is an experimental Owner-private service on a feature branch. Its deterministic state machine and SQLite journal continue multi-stage workflows independently of a Codex Chat turn, because `HOST_TURN_AUTO_RESUME=UNRELIABLE`. Stage runners are adapters; they do not control transitions. A leased singleton lock limits execution to one active job, and interrupted potentially mutating stages require reconciliation rather than blind replay. See `WORKFLOW_SUPERVISOR.md`.

## Codex quota isolation and desktop policy

Daily execution is local-first. Local Qwen performs implementation work through an isolated `CODEX_HOME` whose provider is `qwen_local_bridge`, whose base URL is loopback-only, and whose provider does not require OpenAI authentication. Normal local execution must not fall back to the ChatGPT/OpenAI Codex provider.

OpenAI Codex quota is reserved for planning and explicit acceptance/review work. Codex Desktop is an interactive client, not a 7×24 platform service, and must never be treated as a required daemon for Local Qwen execution. The local platform must continue to function when Codex Desktop is fully quit.

Before and after any task that claims to be local-only, the control plane should be able to snapshot the account Codex rate-limit state. If the five-hour or weekly Codex usage increases during a strictly local task, execution must fail closed with a quota-leak/reconciliation state instead of silently continuing. This guard is telemetry and containment, not authorization to use OpenAI quota.

The accepted provider roles are:

```text
Local Qwen       = default implementation / routine autonomous work
Gemini           = external reviewer / multimodal second opinion after privacy-egress gate
OpenAI Codex     = planning and explicit acceptance/review only
Codex Desktop    = optional interactive UI; never a persistent runtime dependency
```

Local tasks must remain usable with Codex Desktop closed, and no background Desktop mode, Work mode, scheduled UI activity, or global ChatGPT authentication may be considered part of the Local Qwen execution path.
