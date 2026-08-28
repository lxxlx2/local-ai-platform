# local-ai-platform

Local-first multi-provider AI workstation for Apple Silicon.

The project combines local Qwen models, durable workflow state, controlled tool execution, Gemini review, multimodal/media providers, RAG, Telegram control, and Git-backed project artifacts. The goal is a practical production system for coding, research, content/X operations, image/sticker work, novels, speech/video workflows, and future local specialist models.

## Start here

For humans and agents, read in this order:

1. [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md) — current verified project state and active blockers.
2. [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md) — branch, Issue, ADR, qualification, and status rules.
3. [`docs/architecture/INDEX.md`](docs/architecture/INDEX.md) — architecture decision index.
4. [GitHub Issue #14](https://github.com/lxxlx2/local-ai-platform/issues/14) — infrastructure master roadmap.
5. [GitHub Issue #15](https://github.com/lxxlx2/local-ai-platform/issues/15) — product end-state.

Do not infer current production state from an old branch, Issue body, chat transcript, PID, or historical qualification file. `CURRENT_STATUS.md` is the canonical human-readable snapshot, while live runtime evidence overrides stored PID/resource data.

## Current high-level state

- `main` is the stable baseline, not the complete latest feature set.
- Local Qwen3.8 MAIN is qualified and used for local reasoning/production within the currently qualified context envelope.
- Local Qwen3.6 is the FAST/FALLBACK model.
- Local Producer/Supervisor/provider-failover work exists on reviewed or review-pending feature branches.
- Gemini advisory reviewer and privacy/egress path are implemented on feature branches; production merge/activation remains gated.
- Context Architecture V2 is under implementation on `feat/context-budget-manager-v01`.
- Auxiliary model downloads are paused. Whisper, both selected Qwen3-TTS snapshots, and FLUX are physically complete and marker-validated. Embedding, Reranker, LongCat, and RAW-owner targets still need cleanup/resume/qualification. See [`docs/DOWNLOAD_STATUS.md`](docs/DOWNLOAD_STATUS.md).
- No feature-branch completion implies merge, deployment, production activation, external publishing, or permission expansion.

## Source-of-truth hierarchy

Use this order when records disagree:

1. live runtime evidence for current processes/resources;
2. merged `main` for deployed/stable code;
3. `docs/CURRENT_STATUS.md` for cross-branch project state;
4. accepted ADRs in `docs/architecture/`;
5. active implementation Issue + exact branch/commit;
6. qualification evidence;
7. historical Issue bodies/docs;
8. chat memory.

## Repository layout

- `control-plane/` — providers, Supervisor, task/runtime policy, scripts and tests.
- `config/` — versioned non-secret configuration and model/download metadata.
- `docs/` — current status, architecture, governance, qualification, operations.
- `docs/architecture/` — ADRs and architecture index.
- `models/`, `runtime/`, `cache/`, raw logs, credentials and private databases remain local and must never be committed.

Local machine root is normally `/Users/jerson/AI`, but repository code and documentation should avoid treating a user-specific absolute path as a portable architectural contract unless it is explicitly a host-local runtime setting.

## Development workflow

Architecture or policy change:

`Owner approval -> ADR/status sync -> implementation branch -> focused tests -> phase qualification -> independent review -> explicit merge/activation gate`

Ordinary bug fix/refactor:

`feature branch -> focused tests -> qualification as needed -> independent review when required -> merge gate`

Do not create a new docs branch for every architecture decision. Use the implementation branch when safe. If the implementation baseline is frozen for review, use the single long-lived `docs/architecture-ledger` branch for durable ADR/status updates.

## Safety invariants

- Local-first routine production.
- Host/Supervisor owns permissions; models do not grant themselves authority.
- Producer and required independent reviewer must be different providers/roles.
- No silent paid cloud usage.
- PUBLIC/RESTRICTED/PRIVATE privacy routing remains enforced.
- No arbitrary PID killing; process ownership must be exact.
- No secrets, model weights, runtime state, raw credentials, private caches or sensitive databases in Git.
- Downloaded != qualified != deployed.
- Human/Owner approval remains required for merge/deploy/external irreversible actions according to subsystem policy.
