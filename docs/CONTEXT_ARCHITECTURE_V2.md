# Context Architecture V2

Status: OWNER-APPROVED / DOC-SYNCED / IMPLEMENTATION PENDING
Approved: 2026-08-29
Architecture branch: `docs/context-architecture-v2`
Architecture commit: `8c52c29475d45ed0da3147cbc148f880373afce5`
Implementation branch: `feat/context-budget-manager-v01`
Implementation base: `4d229c08adf1ed0716b3922322dca258b625b569`

## Purpose

Unify context handling across Local Qwen, Gemini, chat, coding, X/Twitter operations, novel production, research, media workflows and multimodal tasks.

The architecture separates four concepts that must no longer be conflated:

1. model/native context capacity;
2. locally qualified production context envelope;
3. per-workflow context tier and output reserve;
4. durable memory/retrieval outside the prompt.

Large native context does not automatically become the production default. Production context is promoted only after qualification on the target Mac/runtime.

## Current evidence and problem

Current Local Qwen3.8 MAIN is published at 16,384 total tokens. The sidecar applies that ceiling to chat-template input plus output. Normal Chat context currently uses a much smaller fixed recent-history budget, which underuses the qualified envelope and can make multi-turn chat appear unnecessarily short-memory.

Gemini review currently applies bounded host-side material/output limits for safety and reliability. These are workflow-contract limits, not the model's full native context capability. Large review packages also need explicit output budgeting and longer timeout handling.

Long-running products must not depend on an ever-growing prompt. Git, SQLite/state stores, durable artifacts, event stores, Canon, summaries and retrieval remain the long-term memory layer.

## Core component: Context Budget Manager

Introduce a shared `ContextBudgetManager` used by provider-facing workflows.

Canonical calculation:

```text
qualified_total_context
  - output_reserve
  - system/tool/template_reserve
  - modality reserve when applicable
  = maximum input budget
```

The input budget is then allocated dynamically among recent conversation, summary, retrieved memories, current task/objective, evidence/files/code, web/research evidence and workflow-specific durable artifacts.

The manager must fail closed when the exact provider/runtime token accounting indicates that the request exceeds the qualified envelope.

## Local Qwen tiers

Initial policy while the production runtime remains qualified only to 16K:

### STANDARD

Use the currently qualified 16,384-token total envelope.

Normal chat should dynamically consume available context instead of using a fixed ~3K recent-message ceiling. Output reserve is purpose-dependent.

### LONG candidates

Requalify Qwen3.8 on the target Mac/runtime at 24K and 32K.

Promotion rules:

- 24K may become the normal production default only after functional, memory-pressure, latency, completion and stability qualification.
- 32K should initially be a LONG context tier, not the normal default.
- 64K+ stays out of ordinary production until separately justified and qualified.
- No change to the current 16K production registry is authorized by this document alone.

## Gemini tiers

Gemini context handling becomes token-aware rather than treating a byte ceiling as the primary context measure.

Target host-policy tiers:

```text
STANDARD: up to ~128K input tokens
LONG: up to ~400K input tokens
EXTENDED: up to ~800K input tokens
```

Rules:

- Use provider token-counting support when available before request submission.
- Keep a secondary byte/egress-size limit for abuse/DoS control.
- PUBLIC remains policy-allowed.
- RESTRICTED requires minimization and CloudEgressGate.
- PRIVATE remains denied to Gemini.
- No silent paid billing.
- Timeout/request policy may increase by tier.
- Structured review output remains bounded.

## Workflow policy

### Chat / local response

Replace fixed recent-history budgeting with dynamic budgeting against the currently selected provider's qualified envelope. Summary and retrieved memories must participate in one total budget instead of being appended outside unified accounting.

### Coding / autonomous local work

Context expansion does not replace task decomposition and durable job state. Large repository work uses targeted manifests/retrieval, durable objective/state/test evidence and bounded tool loops.

### X/Twitter operations

Use durable event/evidence storage:

```text
ingest -> dedup -> event store -> ranking -> relevant evidence -> Qwen draft/review
```

Daily work uses relevant current evidence. Monthly/long-horizon reports consume structured daily summaries, major-event indexes and performance/history retrieval rather than raw 30-day source dumps.

### Novel workflows

Preserve Git/Canon/timeline/character state as authoritative durable memory. Retrieve chapter-relevant context. Never flatten an entire long novel into a single prompt by default.

### Commerce / research

Deterministic deduplication/ranking precedes model synthesis. Supply selected evidence plus provenance instead of all collected pages.

### Media / image / video

Qwen3.8 may plan, understand images, create scripts/scene plans/prompts and produce Code Canvas instructions. Pixel generation remains routed to dedicated image providers. TTS/STT/video specialists retain independent runtime contracts.

Durable requirements, production brief, script, scene plan and prompt pack remain cross-stage context boundaries.

### Vision

VISION uses its own exact context budget because image tokens consume the same total model envelope. 24K/32K promotion requires VISION-specific qualification.

## Routing contract

Workflows request capability, purpose, privacy, context tier and expected output class.

Routing/provider evidence records selected provider/model, qualified total context, context tier, input budget, output reserve, retrieval/material selections, privacy/egress decision and fallback reason/history.

Context tier changes do not grant new filesystem, shell, Git, deployment or publishing authority.

## Safety and compatibility

This architecture must not weaken one-heavy-model residency, runtime/provider identity, privacy gates, Owner/public boundaries, producer/reviewer independence, review-before-publish, durable Git/SQLite/Canon state or no-silent-paid-cloud policy.

FLUX, TTS, STT, video, RAG and media contracts do not inherit Qwen/Gemini context numbers automatically.

## Implementation phases

### Phase 1

Dynamic context budgeting under the current 16K Qwen envelope. Implement `ContextBudgetManager`, integrate Chat/Memory, add total-budget tests and keep runtime/model registry at 16K.

### Phase 2

Qwen3.8 24K/32K qualification. Promote only after evidence. 24K is candidate default; 32K initially LONG.

### Phase 3

Gemini token-aware STANDARD/LONG/EXTENDED tiers with per-tier timeout/budget handling and large-review qualification.

### Phase 4

Workflow adoption across coding, X/Twitter, novel, commerce, media and multimodal workflows.

## Non-deployment statement

This document approves architecture and implementation work only. It does not merge any feature branch, change the current production Qwen 16K registry, activate a new Gemini tier, restart services, resume downloads, enable billing, deploy Bot changes or authorize production activation.
