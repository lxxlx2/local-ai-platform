# Bug regressions

| ID | Symptom | Failure layer | Root cause | Regression | Status |
| --- | --- | --- | --- | --- |
| BUG-TG-001 | Raw `**` / headings visible in Telegram chat | Presentation | Model Markdown was sent directly as Telegram plain text; no renderer existed | `test_bug_tg_001_markdown_plain_text_but_code_is_preserved` | Fixed |
| BUG-TG-002 | Chat could end mid-sentence | Provider/application completion boundary | Provider fixed every request at 400 output tokens and discarded Responses completion metadata. A real detailed request returned `incomplete` with `max_output_tokens`. | `test_bug_tg_002_incomplete_is_not_persisted` + real acceptance harness | Fixed |
| BUG-UX-001 | Persistent keyboard and crowded dashboard | Navigation | Legacy ReplyKeyboard was never removed; the inline dashboard exposed 14 mixed-emoji buttons | compact dashboard and ReplyKeyboardRemove regression | Fixed |
| BUG-CHAT-001 | Generic model capability introduction | Intent routing | Product capability questions fell through to free-form chat | `test_bug_chat_001_capability_intro_is_product_aware_and_scoped` | Fixed |
| BUG-TG-003 | Raw fenced-code markers visible | Presentation | Fenced code was treated as literal text by the plain renderer | `test_bug_tg_003_code_fences_are_not_telegram_visible` | Fixed |
| BUG-CODE-001 | Claimed complete Python example omitted an import | Content-quality acceptance | No static check guarded standalone code claims | `test_bug_code_001_complete_examples_are_syntax_checked_and_self_contained` | Fixed |

The renderer removes prose formatting markers while preserving fenced/inline code, JSON, and recognised command literals. Incomplete answers are not persisted as completed assistant messages and are shown with a clear retry message instead of a silent partial answer.
