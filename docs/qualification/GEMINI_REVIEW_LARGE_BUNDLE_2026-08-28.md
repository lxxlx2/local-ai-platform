# Gemini large-bundle review observation 2026-08-28

Status: OBSERVED / IMPLEMENTATION FOLLOW-UP REQUIRED

Context: while using Gemini API as an independent reviewer during Codex quota exhaustion, the first failover review bundle contained 52,751 bytes of material.

Observed path:

```text
GeminiReviewGateway
  -> GeminiReviewerProvider
  -> google-genai Models.generate_content
  -> response_mime_type=application/json
  -> response_json_schema=REVIEW_SCHEMA
  -> max_output_tokens=1536
```

Observed result:

- Gemini API request returned a response;
- response text was incomplete JSON;
- local parsing raised `json.decoder.JSONDecodeError: Unterminated string`;
- provider surfaced `GeminiInvalidOutputError: Gemini returned invalid structured output`;
- the overall multi-bundle review stopped on bundle `01-routing`;
- repository mutation did not occur.

The SDK also emitted an advisory warning recommending Chat-based AFC instead of direct AFC with `Models.generate_content`. This warning is recorded as evidence but is not yet proven to be the root cause of the incomplete JSON response.

Likely implementation risks to investigate:

1. `max_output_tokens=1536` may be insufficient for a large structured review with several findings;
2. the provider currently parses `response.text` with `json.loads` and does not use a structured parsed response when available;
3. `GeminiInvalidOutputError` is currently non-transient and therefore does not try fallback models;
4. one invalid bundle aborts the caller's whole multi-bundle review instead of returning a bounded per-bundle failure;
5. large review bundles should be deliberately bounded by review scope/output budget rather than relying only on the 256 KB input ceiling.

Required follow-up before relying on Gemini as P3 supplementary engineering provider:

- add focused tests for truncated/incomplete structured output;
- inspect current `google-genai` SDK structured response support and prefer a validated parsed payload when available;
- establish a safe output-token budget for code-review results;
- decide whether invalid structured output is eligible for one bounded retry/fallback without masking persistent schema defects;
- ensure per-bundle failures do not destroy already completed review evidence;
- keep Gemini fail closed and retain no mutation authority;
- rerun a real large-bundle review after focused qualification.

This observation does not change the approved provider priority. It is implementation evidence for Issue #18 and the P3 Gemini qualification gate.
