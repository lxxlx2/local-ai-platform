# Gemini P3 Review Qualification

Status: PARTIAL QUALIFICATION / ROUTING + CANCELLATION + DURABLE-STATE REVIEW PASS / FULL P3 QUALIFICATION PENDING

Date: 2026-08-29
Architecture branch: `docs/interactive-provider-priority-v01`
Related issue: #18

## Purpose

Track durable evidence for Gemini API as the P3 supplementary reviewer in the Owner-present interactive provider path.

This document records review capability only. It does not authorize merge, deployment, production activation, direct shell/Git authority, or paid Gemini usage.

## Baseline failure

Initial bounded routing review package size: approximately 52.7 KB.

The first large review failed because Gemini returned truncated structured JSON and the provider raised `GeminiInvalidOutputError`.

Observed weaknesses in the local provider wrapper:

- output budget was 1536 tokens;
- structured output truncation was not robustly handled;
- `GeminiInvalidOutputError` did not participate in model fallback;
- the review prompt did not bound finding count/size.

## Provider robustness work under qualification

Current unmerged fix worktree/branch:

- worktree: `/Users/jerson/local-ai-gemini-review-fix-worktree`
- branch: `fix/gemini-large-review-v01`

Current implementation changes under test:

- raise Gemini structured review output budget to 4096 tokens;
- prefer parsed structured response when available;
- detect MAX_TOKENS structured-output truncation;
- preserve classified Gemini provider exceptions;
- allow bounded model fallback after invalid/truncated structured output;
- bound review response to at most 8 findings;
- add sanitization-aware review instructions;
- distinguish omitted bundle context from repository absence.

Focused local test evidence before real API retest:

- py_compile: PASS
- Gemini/provider/egress focused tests: 16 passed
- git diff --check: PASS

## Real API smoke

Small real Gemini smoke:

- status: `GEMINI_SMOKE_PASS`
- model: `gemini-3.6-flash`
- verdict: PASS
- findings: 0
- latency: 9.849 seconds

The Google SDK emitted an AFC advisory warning. No evidence currently shows that warning caused the earlier structured-output failure.

## Routing bundle review V1

Package size: 52,647 bytes.

Result:

- model: `gemini-3.5-flash`
- verdict: NEEDS_CHANGES
- findings: 2
- latency: 21.033 seconds

Both findings were later proven to be review-representation false positives:

1. A `BLOCKING` finding treated `/Users/<redacted>/...` as literal source text. The actual repository contains a real owner path; `<redacted>` was introduced by the RESTRICTED cloud egress sanitizer.
2. A `MEDIUM` finding claimed two referenced scripts might be absent. Both scripts exist in the repository; the bounded review package simply omitted their contents.

This established that long-context transport worked, while the review contract was not yet sanitization-aware.

## Sanitization-aware contract

The architecture now requires:

- sanitization placeholders are synthetic review artifacts and cannot be treated as literal source defects;
- redacted home/user path components preserve semantic path role while hiding identity;
- omission from a bounded review package does not prove repository absence;
- a bounded path-existence manifest should be supplied where relevant;
- claims depending on omitted context must remain unverified instead of becoming blocking findings;
- privacy rules remain unchanged: PRIVATE denied, RESTRICTED gated/minimized, PUBLIC allowed.

## Routing bundle review V2

Package size: 53,191 bytes.

Path existence manifest:

- `control-plane/scripts/run-codex-qwen-local.sh`: present
- `control-plane/scripts/qualify-codex-qwen-local.sh`: present

Result:

- status: `GEMINI_ROUTING_REVIEW_V2_PASS`
- model: `gemini-3.6-flash`
- verdict: PASS
- findings: 0
- latency: 13.994 seconds
- wall time: 14.006 seconds
- privacy: RESTRICTED
- egress redactions: `email`, `mac_user_path`

Gemini summary stated that the supplied routing/failover mechanisms were fail-closed, tested, maintained state continuity, and showed no confirmed privilege-escalation issue in the bounded supplied evidence.

## Exact cancellation bundle

Package size: 73,277 bytes.

Files reviewed:

- `control-plane/src/local_ai_control/services/supervisor_local_qwen.py`
- `control-plane/src/local_ai_control/services/supervisor_codex.py`
- `control-plane/tests/test_codex_qwen_supervisor.py`

Result:

- status: `PASS_RESPONSE`
- model: `gemini-3.5-flash`
- verdict: PASS
- findings: 0
- latency: 30.957 seconds
- wall time: 30.974 seconds
- privacy: RESTRICTED
- egress redactions: `email`, `mac_user_path`

Review focus included exact `execution_id` ownership, process-group isolation, SIGTERM/SIGKILL behavior, bounded wait/reap, wrong execution ID behavior, concurrency, unrelated-process survival, timeout handling, mutation-fence semantics, and the rule that cancellation proves only that the external process stopped and does not prove no filesystem mutation occurred.

Gemini reported no confirmed defect in the bounded supplied evidence.

## Durable-state and security bundle

Package size: 51,953 bytes.

Files reviewed:

- `control-plane/src/local_ai_control/services/provider_failover.py`
- `control-plane/src/local_ai_control/services/supervisor_codex.py`
- `control-plane/src/local_ai_control/services/codex_failover_adapter.py`
- `control-plane/tests/test_provider_failover.py`
- `control-plane/tests/test_codex_failover_adapter.py`

Result:

- status: `PASS_RESPONSE`
- model: `gemini-3.5-flash`
- verdict: PASS
- findings: 0
- latency: 31.307 seconds
- wall time: 31.329 seconds
- privacy: RESTRICTED
- egress redactions: `email`, `mac_user_path`

Review focus included same-job continuity, objective/worktree/branch binding, candidate and validation evidence preservation, append-only/idempotent provider history, repeated-signal behavior, handoff/preflight durability, blocked-state preservation, safe-boundary recovery, fail-closed ambiguous evidence, local-route attribution, no accidental OpenAI runner, no Local-Qwen self-approval/commit/push/merge/deploy authority, and repository/workspace mismatch handling.

Gemini reported no confirmed defect in the bounded supplied evidence.

## Current capability conclusion

Evidence currently supports:

- Gemini API can process bounded code-review packages from roughly 52 KB to 73 KB within tens of seconds;
- structured JSON output can succeed after provider robustness changes;
- sanitization-aware review packaging removes confirmed false positives observed in V1;
- routing/failover review: PASS;
- exact cancellation review: PASS;
- durable-state/security review: PASS;
- Gemini is technically suitable as a fast independent long-context reviewer for the P3 role, subject to the remaining provider-fix and three-provider qualification gates.

These model-review results are independent review evidence, not a substitute for executable tests or Owner/host acceptance.

Evidence does not yet support full P3 qualification.

Remaining required evidence:

1. broader Gemini provider regression tests;
2. full control-plane test suite on the provider robustness branch;
3. final real Gemini smoke after the complete fix;
4. commit and push of `fix/gemini-large-review-v01`;
5. independent review of the Gemini provider robustness fix by a provider other than Gemini;
6. P2 -> P3 supplementary routing implementation remains pending;
7. Gemini structured patch/continuation provider remains pending;
8. cross-provider `producer != final independent reviewer` enforcement remains pending;
9. full three-provider routing/independent-review qualification remains pending.

## Safety status

- Gemini direct shell authority: DENIED
- Gemini direct Git mutation authority: DENIED
- Gemini deploy/service authority: DENIED
- silent paid Gemini usage: DENIED
- production activation: NO
- merge authorization: NO
