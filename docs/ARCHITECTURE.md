# Local AI Platform architecture

Telegram is the primary gateway. Telegram-authenticated user IDs create an immutable `IdentityContext`; deterministic code then routes each request to the Owner or Public plane. The language model is used only for text generation, summaries, and future validated intent parsing. It has no authority to authenticate, access files, operate Git, inspect system state, or execute commands.

```text
Telegram → Bot ingress → Identity router → Authorization → deterministic service/queue → AI router → oMLX
                                      ├─ Owner: private DB, private tasks/projects
                                      └─ Public: public DB, public sandbox
```

Qwen3.6 is the fast/default local text model. Default chat context is 8K; complex owner tasks and research may use 32K; 64K remains a special long-context mode. Qwen3.6 is not a coding-agent replacement.

## Program-level workflow supervision

Workflow Supervisor V0.1 is an experimental Owner-private service on a feature branch. Its deterministic state machine and SQLite journal continue multi-stage workflows independently of a Codex Chat turn, because `HOST_TURN_AUTO_RESUME=UNRELIABLE`. Stage runners are adapters; they do not control transitions. A leased singleton lock limits execution to one active job, and interrupted potentially mutating stages require reconciliation rather than blind replay. See `WORKFLOW_SUPERVISOR.md`.
