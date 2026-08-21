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

Navigation acceptance additionally asserts the final `/start` message sequence (temporary cleanup removed, dashboard retained), scoped Inline Keyboards, bounded Owner/Public first pages, and parent-route callback results for every detail page. Telegram presentation acceptance inspects final payload text, `parse_mode`, balanced native entities, reconstructed visible text, message order, and zero content loss/duplication—not only renderer source.

Code-answer acceptance has three honest levels: `UNVALIDATED`, `STATIC_VALIDATED`, and `SANDBOX_EXECUTION_VALIDATED`. `GENERAL_MODEL_CODE_EXECUTION=UNSUPPORTED`: unknown model output is never executed and cannot reach execution validation. Static validation covers syntax, required imports, documented-type consistency, and decorator control-keyword consumption. Golden execution uses a normal host subprocess only for an exact hash-pinned, audited synthetic fixture; it is **not** a general OS-level filesystem/network sandbox. Safety relies on the exact source hash, AST capability denial, ephemeral working directory, sanitized environment, no shell, CPU/file/process limits, a parent-enforced memory ceiling, and timeout. “Runnable validated” must never be claimed for lower levels.

Codex host turn auto-resume across detached reviewer lifecycles is not controlled by this repository and is treated as unreliable. Durable workflow state and exact reviewer-result reconciliation are the supported recovery mechanism; documentation or prompt rules must not claim host-level turn control.

Private Learning acceptance covers Owner/Public isolation, Secret-before-persistence, privacy redaction, bounded content storage, deterministic splits and deduplication, permanent Golden isolation, immutable dataset versions, preference pairs, verified business outcomes, exact eval-to-adapter binding, single-active rollback, safe import/export, and dry-run retention. MLX acceptance uses read-only capability probing and deterministic disabled-by-default config; it does not require or permit a training run against the live service.
