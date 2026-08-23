# local-ai-platform

Apple Silicon local AI infrastructure for oMLX deployment, model evaluation,
benchmarks, service configuration templates, shared scripts, architecture
documentation, future Codex Local integration, private Tailscale access, and
future model evaluation. Models remain local and are never committed; secrets
are never committed; business repositories remain independent.

Current deployed V1 fallback model: `mlx-community/Qwen3.6-35B-A3B-4bit`.
The production-capabilities branch qualifies `mlx-community/Qwen3.8-27B-8bit`
as 16K MAIN/VISION and wires it through an isolated localhost-only runtime;
deployment still requires the explicit service-restart gate.

Current status: 8K PASS, 32K PASS, 64K Special Long Context Mode, and 30-minute
stability with 30/30 API success. Structured Tool Calling is pending independent
revalidation.

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
