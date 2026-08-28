# Context Architecture V2

Status: OWNER-APPROVED / DOC-SYNCED / IMPLEMENTATION PENDING
Approved: 2026-08-29
Branch: `docs/context-architecture-v2`
Base: `617dff7b0802d393cde3a49cb1ee48b66fa18e08`

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

The input budget is then allocated dynamically among:

```text
recent conversation
summary
retrieved memories
current task/objective
evidence/files/code
web/research evidence
workflow-specific durable artifacts
```

The manager must fail closed when the exact provider/runtime token accounting indicates that the request exceeds the qualified envelope.

## Local Qwen tiers

Initial policy while the production runtime remains qualified only to 16K:

### STANDARD

Use the currently qualified 16,384-token total envelope.

Normal chat should dynamically consume available context instead of using a fixed ~3K recent-message ceiling. Output reserve is purpose-dependent.

Example target budgets before exact tokenizer accounting:

```text
normal chat: ~1K output reserve
long answer: up to ~4K output reserve
coding/task work: ~2K output reserve
vision: image/token cost + text + output must fit one exact total budget
```

### LONG candidates

Requalify Qwen3.8 on the target Mac/runtime at:

```text
24K
32K
```

Promotion rules:

- 24K may become the normal production default only after functional, memory-pressure, latency, completion and stability qualification.
- 32K should initially be a LONG context tier, not the normal default.
- 64K+ stays out of ordinary production until separately justified and qualified.
- No change to the current 16K production registry is authorized by this document alone.

Qualification must cover at least chat, coding/task synthesis and VISION where applicable, plus exact tokenizer accounting, output completion, memory pressure, swap behavior and timeout/TTFT observations.

## Gemini tiers

Gemini context handling becomes token-aware rather than treating a byte ceiling as the primary context measure.

Target tiers:

```text
STANDARD: up to ~128K input tokens
LONG: up to ~400K input tokens
EXTENDED: up to ~800K input tokens
```

These are host policy ceilings, not claims about model-native limits.

Rules:

- Use provider token-counting support when available before request submission.
- Keep a secondary byte/egress-size limit for abuse/DoS control.
- PUBLIC is allowed according to provider policy.
- RESTRICTED requires minimization and CloudEgressGate.
- PRIVATE remains denied to Gemini.
- No silent paid billing.
- Timeout and request policy may increase by tier.
- Structured review output remains bounded. Initial review target stays concise, with a bounded findings count and explicit output reserve.
- Large review failure must not authorize direct Gemini filesystem/Git/shell mutation.

## Workflow policy

### Chat / local response

Replace fixed recent-history budgeting with dynamic budgeting against the currently selected provider's qualified envelope. Summary and retrieved memories must participate in one total budget instead of being appended outside a unified accounting model.

### Coding / autonomous local work

Context expansion is useful but does not replace task decomposition and durable job state. Large repository work should use targeted manifests/retrieval, durable objective/state/test evidence and bounded tool loops. Qwen LONG context is an additional capability, not permission for unbounded full-repository ingestion.

### X/Twitter operations

Use durable event/evidence storage:

```text
ingest -> dedup -> event store -> ranking -> relevant evidence -> Qwen draft/review
```

Daily work uses relevant current evidence. Monthly/long-horizon reports consume structured daily summaries, major-event indexes and performance/history retrieval rather than raw 30-day source dumps.

### Novel workflows

Preserve Git/Canon/timeline/character state as authoritative durable memory. Retrieve only chapter-relevant context. Never flatten an entire long novel into a single prompt by default.

### Commerce / research

Deterministic deduplication/ranking precedes model synthesis. Supply selected evidence plus provenance instead of all collected pages.

### Media / image / video

Qwen3.8 may plan, understand images, create scripts/scene plans/prompts and produce Code Canvas instructions. Pixel generation remains routed to dedicated image providers such as FLUX/Qwen-Image candidates. TTS/STT/video specialists retain independent runtime contracts.

`requirements`, `production_brief`, `script`, `scene_plan`, `prompt_pack` and related durable artifacts remain the cross-stage context boundary.

### Vision

VISION uses its own exact context budget because image tokens consume the same total model envelope. 24K/32K promotion requires VISION-specific qualification before callers assume those tiers are available for multimodal input.

## Routing contract

Workflows request:

```text
capability
purpose
privacy
context tier
expected output class
```

The router/provider returns or records:

```text
selected provider/model
qualified total context
context tier
input budget
output reserve
retrieval/material selections
privacy/egress decision
fallback reason/history
```

Provider changes must preserve durable task identity and state. Context tier changes do not grant new filesystem, shell, Git, deployment or publishing authority.

## Safety and compatibility

This architecture must not weaken:

- one-heavy-model residency policy;
- exact local runtime/provider identity;
- privacy/egress gates;
- Owner/public capability boundaries;
- producer/reviewer independence;
- review-before-publish behavior;
- Git/SQLite/Canon durable state;
- no silent paid cloud usage.

Existing FLUX, TTS, STT, video, RAG and media contracts do not inherit Qwen/Gemini context numbers automatically.

## Implementation phases

### Phase 1: dynamic context budgeting under current 16K Qwen envelope

Implement `ContextBudgetManager`, integrate Chat/Memory, add exact total-budget tests, and keep runtime/model registry at 16K.

### Phase 2: Qwen3.8 24K/32K qualification

Run controlled local qualification. Promote only after evidence. 24K is the candidate default; 32K is initially LONG.

### Phase 3: Gemini token-aware tiers

Add token counting, STANDARD/LONG/EXTENDED host policy, per-tier timeout/budget handling and robust large-review qualification.

### Phase 4: workflow adoption

Adopt context-tier declarations and retrieval/material budgeting across coding, X/Twitter, novel, commerce, media and multimodal workflows.

## Non-deployment statement

This document approves architecture and implementation work only. It does not merge any feature branch, change the current production Qwen 16K registry, activate a new Gemini tier, restart services, resume downloads, enable billing, deploy Bot changes or authorize production activation.
