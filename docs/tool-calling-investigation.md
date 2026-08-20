# Tool-calling investigation

oMLX 0.6.3rc1 exposes both `/v1/chat/completions` and `/v1/responses`. Its server passes supplied tools into the model chat template, then parses native parser output or Qwen's `<tool_call><function=...>` XML into structured calls.

- Chat Completions returns structured calls in `choices[0].message.tool_calls`, with `id`, `function.name`, and JSON-string `function.arguments`; its finish reason is `tool_calls` when parsed.
- Responses accepts flat function tools (`type`, `name`, `description`, `parameters`) and produces `output` items of type `function_call` with `call_id`, `name`, and JSON-string `arguments`.
- Responses input supports `function_call_output`; persisted response chains are also supported via `previous_response_id`.
- Both endpoints have explicit streaming paths that emit structured tool-call events. Responses emits `response.function_call_arguments.delta` and `.done` events.

The model template accepts `tools`; it serializes tool definitions and instructs XML function-call output. `enable_thinking=false` remains part of every harness request. This document records implementation inspection only; no third-party code was patched.
