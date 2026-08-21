# Engineering acceptance

## Quality governance

The lifecycle is now Producer → deterministic validation → self acceptance → independent review → security gate → acceptance ready → user UX acceptance. The producer cannot self-approve; see `INDEPENDENT_REVIEW.md` and `QUALITY_GATES.md`.

## Self-Acceptance First

`pytest PASS` does not equal product acceptance. Each change must follow this order:

```text
code → unit → integration → real local service → security → acceptance harness → self acceptance → user UX acceptance
```

The final user test is a small visual and interaction gate, not outsourced QA. Reproduce every user-found defect with a stable fixture, document the failure layer and root cause, and keep the regression test permanently.

For Telegram chat, acceptance includes the actual provider, canonical persistence, the shared output renderer, chunk reconstruction, a capture transport, and security regressions. Runtime reports contain metrics only and remain Git-ignored.

Navigation acceptance additionally asserts that `/start` removes legacy ReplyKeyboard UI, dashboards are scoped Inline Keyboards, first-level Owner/Public button counts remain bounded, and page navigation edits the active dashboard message. Code-answer acceptance includes raw-fence absence, code-literal preservation, and Python AST/import checks for claimed standalone examples.
