# Structured tool-calling revalidation — 2026-08-21

Status: **PARTIAL**. All tests used localhost only, `enable_thinking=false`, and deterministic local mocks; no secret or external service was used.

## Structured API results

- Chat Completions C1–C5: PASS. `auto`, `required`, specific-function selection, `add_numbers(137,285)`, and no-tool-needed behavior returned the expected structured or text result.
- Responses R1–R5: PASS. Responses returned `output` items of type `function_call` with `call_id`, function name, and valid JSON arguments; specific selection is supported in this build.
- Responses round trip: PASS. A `get_weather(Bangkok)` function call was returned, the deterministic mock result was sent as `function_call_output` with `previous_response_id`, and the final answer correctly used “31°C, partly cloudy”.
- Reliability: **19/20** structured Chat calls returned a valid function id/name/JSON arguments, meeting the stated 19/20 threshold. One call did not meet the harness's structured-call assertion; no API errors occurred.

## Not yet complete

Chat round trips, five total multi-turn round trips, streaming tool-call event assembly, and the isolated Codex-like sequence were not completed in this run. Therefore `CODEX_LOCAL_READY=NO` and tool calling cannot yet be marked production-ready for Codex Local.

## Investigation

The model template accepts tools and emits XML tool-call markup; oMLX parses it to OpenAI-compatible structures. The earlier stability harness observed no `delta.tool_calls` chunks because it only inspected streaming deltas; source inspection shows oMLX can emit structured calls at final stream completion. This is a supported hypothesis, not a conclusive streaming validation.

Raw API responses are stored locally in the git-ignored `tool-calling-revalidation-raw.json` artifact.
