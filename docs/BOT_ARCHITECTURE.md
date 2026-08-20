# Telegram control-plane architecture

V0.1 uses Python and aiogram (mature async Telegram framework, isolated from oMLX) for long polling. The bot is Chinese-button-first; a future Mini App is a detail viewer, not a replacement control plane. SQLite runtime data, bot token, and owner id are under ignored `runtime/`; the repository contains no credential.

Qwen3.6 is the FAST/default local model for single-request generation, classification, summaries, and strict-JSON intent parsing. It is not a Coding Agent or Codex replacement because real multi-step agent-loop acceptance failed. Open WebUI remains optional debug UI and is not installed.
