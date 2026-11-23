# Qwen3.8 Codex Desktop isolated API path

Status: STAGE_1_SCRIPT_READY / NOT_EXECUTED_ON_OWNER_MAC  
Tracker: Issue #44

## Goal

Use the already-downloaded Qwen3.8-27B 8-bit as a **local fallback for Codex Desktop** when GPT/Claude quotas are exhausted, while leaving the Owner's normal Codex sessions, Claude API and Codex Web Bridge untouched.

## Verified constraints

- Existing Claude LiteLLM: 127.0.0.1:48721, PID 34714 observed on Owner Mac. Do not inspect private secrets, modify settings, or stop.
- Existing codex-web-bridge: 127.0.0.1:8199. Do not contact or alter.
- Normal Codex Desktop config, logins, sessions, OAuth and accounts are protected.
- Qwen3.8 weights: /Users/jerson/AI/models/qwen38-27b-8bit (about 28G on disk).
- Current local-ai Qwen3.8 sidecar supports string-only \`input\` at \`/v1/responses\`, with neither Responses tool calls nor Codex-compatible streamed event handling. Therefore a successful text reply is **NOT** successful Codex integration.
- Current resource policy: expected runtime memory 34 GiB; 85% reclaimable gate, 4 GiB system reserve, 2 GiB per-run swap growth cap, 6 GiB absolute swap cap. No overrides for this experiment.
- Existing model path and main/FALLBACK alias remain unchanged.

## Stage 1: isolated admission and text API smoke

This is a bounded functional gate. Default command performs preflight only and does not start the model.

Run from macOS Terminal:

\`\`\`bash
cd /Users/jerson/AI
git fetch origin
git show origin/feat/qwen38-codex-isolated-api-smoke:control-plane/scripts/probe-qwen38-codex-api.py > /tmp/probe-qwen38-codex-api.py
/Users/jerson/AI/runtime/control-plane-venv/bin/python /tmp/probe-qwen38-codex-api.py
\`\`\`

If preflight reports \`PREFLIGHT_PASS_ONLY\`, run:

\`\`\`bash
/Users/jerson/AI/runtime/control-plane-venv/bin/python /tmp/probe-qwen38-codex-api.py --smoke
\`\`\`

If \`RESOURCE_BLOCKED\`: stop. This is valid evidence that the existing 8-bit deployment cannot be admitted under current normal office workload. Do not close Chrome/Codex or loosen policy to manufacture PASS.

If \`API_TEXT_PASS\`: only direct Qwen text inference and API health were proven. The sidecar must still be extended or replaced to correctly handle Codex Responses streaming, function_call/function_call_output, multi-turn input/tool state, interruption and compaction.

Evidence: \`/Users/jerson/AI/runtime/direct-work-validation/qwen38-codex-api/<run-id>/result.json\`. It contains no credentials or private Codex sessions.

## Stage 2: tool-protocol bridge, only if Stage 1 passes

Implement in an isolated process/port and prove a **real** Responses tool-call round trip; never reconfigure the production sidecar, original LiteLLM, or shared Codex settings. Model output rendered as textual XML/JSON is a failure if no executable function_call event reaches Codex.

Required assertions:
- SSE event format and final response/turn boundaries are understood by Codex;
- tool schemas sent by Codex are preserved and mapped to Qwen's native tool template;
- valid function_call \`call_id\` returned and actual \`function_call_output\` accepted on continuation;
- no tool call replay after timeout, retry, cancellation or context compression;
- no false success from ordinary text pretending to execute a command.

The Qwen3.8 model template includes a \`<tool_call>\` format, but the existing sidecar does not parse or expose it as OpenAI Responses tool objects.

## Stage 3: isolated Codex Desktop, only if Stage 2 passes

Use an isolated \`CODEX_HOME\` and separate Electron user-data directory, **without overwriting** \`~/.codex\`, \`~/.config/codex-claude\`, the Claude LiteLLM listener or Web Bridge. Note upstream Codex Desktop bug reports about cross-home SQLite state and custom-provider sessions; do not claim full isolation without verifying actual process paths.

Do not invoke \`codex-mode\`, \`codex-uwa\`, \`codex-official\`, \`codex-uwa-stop\`, nor restart active Codex Desktop during initial testing.

The E2E test must require one real file read, one real code edit, one real test execution, and a verifiable resulting diff in a disposable test project. A model simply describing these actions is not a PASS.

## Promotion decision

Only \`WORKFLOW_PASS\` for \`CODEX_DESKTOP_LOCAL_FALLBACK\` after safe representative workload + actual Desktop E2E. No claim of parity with GPT/Claude until enough real work samples are observed. No changes to existing user routing or model deletions without separate approval.

## References

- \`docs/MODEL_WORK_CAPABILITY_VALIDATION_V1.md\`
- \`docs/qualification/WORKLOAD_QUALIFICATION_POLICY.md\`
- \`control-plane/src/local_ai_control/services/qwen38_runtime.py\`
- \`lxxlx2/codex-web-bridge\` independent project and modes
- OpenAI Codex config provider documentation; LiteLLM Codex Desktop guide

## Actual Owner Mac result: 2026-11-24 Bangkok (run 2026-11-23T17:30:32Z)

Evidence artifact (private local file, not uploaded):
`/Users/jerson/AI/runtime/direct-work-validation/qwen38-codex-api/20261123T173032Z/result.json`

Owner-supplied terminal result, no independently uploaded JSON content:

```text
PREFLIGHT_ALLOWED = False
PREFLIGHT_REASON = INSUFFICIENT_RECLAIMABLE_MEMORY
RECLAIMABLE_GIB = 25.166595458984375
SWAP_GIB = 0.0
RESULT = RESOURCE_BLOCKED
CLEANUP_OK = True
CODEX_TOOL_E2E = NOT_TESTED
PROTECTED_PORTS_ACCESSED = False
```

With current 8bit Qwen3.8 expected-memory budget of 34 GiB and the unchanged admission rule 85%, 28.90 GiB reclaimable is required. About 3.73 GiB was missing. No model started, so **there is no text API success, function-call success, or Codex Desktop integration evidence**.

**Decision for this exact model/deployment:** `QWEN38_27B_8BIT x CODEX_DESKTOP_LOCAL_FALLBACK = RESOURCE_BLOCKED / DO_NOT_USE` under representative office workload. Do not repeat preflight without a meaningful change in actual resource conditions or model deployment. Preserve existing Codex Desktop, Claude API LiteLLM (127.0.0.1:48721) and codex-web-bridge (127.0.0.1:8199).

**Candidate, not yet downloaded/tested:** Qwen3.8-27B MLX 6bit, separate directory/runtime, subject to new model-specific resource budget and tool-call E2E. The quantization change can decrease resource requirements but may affect answer quality. No promotion before actual results; keep 8bit weights until replacement has passed and separate deletion is authorized.
