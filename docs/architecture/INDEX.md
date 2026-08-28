# Architecture Decision Index

This index is the canonical entrypoint for architecture decisions. The detailed implementation state lives in `docs/CURRENT_STATUS.md`.

| ADR | Decision | Status | Implementation / evidence |
|---|---|---|---|
| [ADR-0001](ADR-0001-local-first-provider-policy.md) | Local-first provider and authority policy | ACCEPTED | Issues #14, #15; multiple feature branches |
| [ADR-0002](ADR-0002-interactive-provider-priority.md) | Owner-present interactive engineering priority: Codex > Qwen > Gemini supplementary | ACCEPTED / PARTIALLY IMPLEMENTED | Issue #18; `feat/codex-desktop-auto-failover-v01`; Gemini reviewer fix branch |
| [ADR-0003](ADR-0003-context-architecture-v2.md) | Dynamic Context Budget Manager and task-specific context tiers | ACCEPTED / IMPLEMENTING | Issue #19; `feat/context-budget-manager-v01` |
| [ADR-0004](ADR-0004-repository-governance-v2.md) | ADR ledger + canonical current status + reduced docs-branch sprawl | ACCEPTED / IMPLEMENTING | `docs/architecture-ledger` |

## Status meanings

- `PROPOSED`: not approved.
- `ACCEPTED`: Owner-approved architecture.
- `IMPLEMENTING`: implementation exists but is incomplete/unqualified.
- `PARTIALLY IMPLEMENTED`: some contract is proven while later phases remain.
- `QUALIFIED`: required evidence passed for the stated scope.
- `SUPERSEDED`: retained for history; a newer ADR controls.
- `RETIRED`: no longer applicable.

`ACCEPTED` never means merged or deployed. Check `CURRENT_STATUS.md` for branch, review, merge and production state.
