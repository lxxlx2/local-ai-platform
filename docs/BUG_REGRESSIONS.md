# Bug regressions

| ID | Symptom | Failure layer | Root cause | Regression | Status |
| --- | --- | --- | --- | --- |
| BUG-TG-001 | Raw `**` / headings visible in Telegram chat | Presentation | Model Markdown was sent directly as Telegram plain text; no renderer existed | `test_bug_tg_001_markdown_plain_text_but_code_is_preserved` | Fixed |
| BUG-TG-002 | Chat could end mid-sentence | Provider/application completion boundary | Provider fixed every request at 400 output tokens and discarded Responses completion metadata. A real detailed request returned `incomplete` with `max_output_tokens`. | `test_bug_tg_002_incomplete_is_not_persisted` + real acceptance harness | Fixed |

The renderer removes prose formatting markers while preserving fenced/inline code, JSON, and recognised command literals. Incomplete answers are not persisted as completed assistant messages and are shown with a clear retry message instead of a silent partial answer.
