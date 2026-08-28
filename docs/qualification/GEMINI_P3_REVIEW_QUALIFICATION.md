# Gemini P3 Review Qualification

Status: PARTIAL QUALIFICATION / ROUTING BUNDLE PASS / FULL P3 QUALIFICATION PENDING

Date: 2026-08-28
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

## Current capability conclusion

Evidence currently supports:

- Gemini API can process a bounded code-review package around 53 KB within tens of seconds;
- structured JSON output can succeed after provider robustness changes;
- sanitization-aware review packaging removes confirmed false positives observed in V1;
- Gemini is a promising independent long-context reviewer for P3.

Evidence does not yet support full P3 qualification.

Remaining required review evidence:

1. exact process/execution cancellation bundle;
2. durable same-job handoff, provider history and mutation-fence/security bundle;
3. broader Gemini provider regression tests;
4. commit and push of the provider robustness fix;
5. independent review of the provider robustness fix by a provider other than Gemini if Gemini materially produced/approved the implementation;
6. full three-provider routing/independent-review qualification remains pending.

## Safety status

- Gemini direct shell authority: DENIED
- Gemini direct Git mutation authority: DENIED
- Gemini deploy/service authority: DENIED
- silent paid Gemini usage: DENIED
- production activation: NO
- merge authorization: NO
