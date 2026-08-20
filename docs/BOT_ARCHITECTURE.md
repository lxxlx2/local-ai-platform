# Telegram control-plane architecture

## V0.2 unified gateway

`@Jersonliu_bot` is the unified Telegram AI gateway. Telegram user ID is converted once into immutable Owner or Public identity context; each plane has backend authorization and distinct local development data stores. The running V0.1 Bot is deliberately not restarted by this development phase.

Public AI has no private/system tools. Owner ordinary chat also has no shell, filesystem, Git, or system tools. A later control request must use schema validation, authorization, preview, confirmation, and deterministic execution.

V0.1 uses Python and aiogram (mature async Telegram framework, isolated from oMLX) for long polling. The bot is Chinese-button-first; a future Mini App is a detail viewer, not a replacement control plane. SQLite runtime data, bot token, and owner id are under ignored `runtime/`; the repository contains no credential.

Qwen3.6 is the FAST/default local model for single-request generation, classification, summaries, and strict-JSON intent parsing. It is not a Coding Agent or Codex replacement because real multi-step agent-loop acceptance failed. Open WebUI remains optional debug UI and is not installed.
