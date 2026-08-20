# Telegram AI Control Plane V0.1

Telegram is the primary control plane. The deterministic application—not an LLM—owns task transitions, permissions, approvals, and audit events. Models only provide single-request analysis or structured intent suggestions.

Runtime secrets and SQLite data stay under `~/AI/runtime/control-plane/` and are never committed. Long polling is the only intended Telegram network mode; no webhook, public port, Serve, or Funnel is used.
