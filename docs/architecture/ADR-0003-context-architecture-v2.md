# ADR-0003: Context Architecture V2

Status: ACCEPTED / IMPLEMENTING

## Decision

Context handling is managed by a host-side Context Budget Manager rather than independent hard-coded prompt limits scattered across chat, coding, X, novels, research and media workflows.

## Core rules

- Distinguish native model context, qualified production context, task input budget and output reserve.
- Qwen production context remains at the currently qualified ceiling until a separate 24K/32K qualification promotes it.
- Normal chat may dynamically use more of the qualified envelope while preserving compatibility for older direct ContextAssembler callers.
- Long-lived workflow state belongs in Git/SQLite/Canon/event stores/RAG, not one ever-growing prompt.
- Gemini uses task-specific context tiers later, with privacy/egress controls and token-aware accounting.
- Vision/media workflows receive modality-specific reserves rather than inheriting one text-chat constant.

## Planned phases

1. ContextBudgetManager + Chat/Memory integration under current Qwen ceiling.
2. Qwen 24K/32K qualification before promotion.
3. Gemini STANDARD/LONG/EXTENDED token-aware tiers.
4. Shared context-tier/retrieval contracts across coding, X, novels, commerce, media and multimodal workflows.

## Tracking

Issue #19 and `feat/context-budget-manager-v01`.

Current Phase 1 evidence: compatibility regression PASS, focused context/gateway suite PASS, full control-plane suite 686 PASS. Final static verification/commit/push/review remain pending.
