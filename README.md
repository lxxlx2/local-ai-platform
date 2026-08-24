# local-ai-platform

Apple Silicon local AI infrastructure for oMLX deployment, model evaluation,
benchmarks, service configuration templates, shared scripts, architecture
documentation, future Codex Local integration, private Tailscale access, and
future model evaluation. Models remain local and are never committed; secrets
are never committed; business repositories remain independent.

Canonical current status and new-conversation handoff:
[`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md).

Production MAIN/VISION is `mlx-community/Qwen3.8-27B-8bit`, qualified through
16K and managed on localhost. FAST/FALLBACK is the downloaded and validated
`mlx-community/Qwen3.6-35B-A3B-4bit` through on-demand oMLX. Real-Mac
MAIN → FAST → MAIN and cold FAST → MAIN lifecycle validation passed.

Workflow Supervisor Stage 5A is currently blocked by exact start-identity
capture and remains stopped. Real Codex execution is disabled. The Gemini MCP
bridge is roadmap-only and not implemented; see
[GitHub Issue #1](https://github.com/lxxlx2/local-ai-platform/issues/1).
Auxiliary downloads are intentionally paused with partial data preserved; see
[`docs/MODEL_DOWNLOAD_QUEUE.md`](docs/MODEL_DOWNLOAD_QUEUE.md).

## Local layout

This directory is the local, non-Git home for AI infrastructure. It is separate from all existing business repositories.

- `models/`: downloaded model weights; never commit.
- `cache/`: reconstructible downloads and inference caches; never commit.
- `runtime/`: sockets, PIDs, and transient state; never commit.
- `logs/`: local operational logs; do not commit raw logs.
- `inbox/` and `output/`: transient file-processing exchange areas.
- `benchmarks/`: reproducible local benchmark records.
- `config/`: local configuration templates only; no secrets.
- `docs/`: V1 operational and architecture documentation.
- `tmp/`: disposable work area.

V1 safety rules: local-first, GitHub as canon for approved project artifacts, one large LLM loaded at a time, localhost-first API binding, Tailnet rather than public exposure, and human approval for external publishing.

Models, caches, runtime state, logs, databases, and secrets remain local and
must never be committed.
