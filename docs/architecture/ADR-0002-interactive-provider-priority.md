# ADR-0002: Interactive engineering provider priority

Status: ACCEPTED / PARTIALLY IMPLEMENTED

## Decision

For Owner-present interactive engineering initiated from Codex Desktop, provider priority is:

1. OpenAI Codex when available.
2. Local Qwen3.8 MAIN fallback.
3. Gemini API supplementary provider when Qwen cannot safely continue or an independent cloud capability is useful and privacy permits.

The broader unattended/routine platform remains local-first under ADR-0001.

## Durable-task requirement

Provider changes must preserve the same durable task identity, project/worktree, branch, objective, candidate/evidence state and review history. Exact same-chat hot swap is desirable but not required.

## Review independence

`producer != final independent reviewer`

A model/provider may self-check but cannot satisfy the final independent-review requirement for its own candidate.

## Authority boundaries

- Qwen fallback cannot self-approve, merge, deploy or expand permissions.
- Gemini supplementary output must be host-controlled structured proposals/continuations, not ambient machine authority.
- Merge/deploy/production actions remain Owner/host gated.

## Tracking

Issue #18 is the implementation tracker.

Qualified foundations include Codex -> Qwen failover, durable provider history, exact cancellation and advisory Gemini review. Full three-provider supplementary execution remains incomplete.
