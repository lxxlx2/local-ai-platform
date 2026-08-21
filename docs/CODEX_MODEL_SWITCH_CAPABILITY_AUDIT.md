# Codex Model / Provider Capability Audit

Audited 2026-08-21 against local `codex-cli 0.146.0` and current official Codex documentation. The local user config currently has no `model_provider`, `model_providers`, `review_model`, or provider profiles configured. No Codex config was changed for this phase.

| Capability | Result | Safe use in this project |
|---|---|---|
| Native model selector / `/model` | Account/client capability; enumerate actual choices before use | Do not claim or switch this current session |
| `/review` | Supported; reviews a commit/diff without modifying the worktree | Used as project policy; Round A used an isolated read-only reviewer |
| Detached review | Supported in Settings → General → Code review | Prefer for future reviews; not changed automatically |
| `review_model` | Supported user-level config key | Not configured without an account-available validated model |
| `model_providers` | Supported only in user-level config; Responses wire protocol | Model registry prepares metadata only; no config write |
| Profiles | Supported as `$CODEX_HOME/profile-name.config.toml`, selected with `--profile` | Future profiles must be user-level, backed up, and separately validated |
| App Server | Supports model listing and config APIs | Future localhost-only adapter may show status; not used to alter this session |
| Plugins | Can expose status/orchestration; app-server plugin install endpoints remain under development | No plugin installed or created here |

Official restrictions applied: project-local `.codex/config.toml` cannot override provider/auth/profile keys; custom provider `wire_api` is Responses only; direct bearer tokens are discouraged in favor of `env_key` or command-backed auth. The platform therefore stores aliases only and uses a local Keychain setup helper for a future credential.

Current Codex session provider changed: **No**. Current Qwen3.6 compatibility: oMLX exposes `/v1/responses`, so it is a local protocol candidate; its coding-agent validation is still **No** and it is never set as the Coding role.
