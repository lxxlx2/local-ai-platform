# Qualification Evidence Index

This directory stores durable qualification and review evidence that should remain discoverable after temporary feature/docs branches are retired.

Canonical rules:

- Qualification evidence records what was actually tested or observed.
- A model/provider review result does not replace executable tests.
- A qualification PASS does not imply merge, deployment, production activation, permission expansion or paid usage.
- Historical failures are preserved when they materially explain later architecture/provider decisions.
- `docs/CURRENT_STATUS.md` remains the canonical cross-branch status snapshot.

Current preserved evidence:

- `GEMINI_REVIEW_LARGE_BUNDLE_2026-08-28.md` — initial large structured-review failure and follow-up requirements.
- `GEMINI_P3_REVIEW_QUALIFICATION.md` — bounded review bundles, provider robustness regression and remaining non-Gemini/P3 gates.
- `QWEN_BOUNDED_REVIEW_2026-08-29.md` — two-file independent review attempt that timed out at 300 seconds with no verdict after infrastructure blockers were removed.

When an old branch is retired, migrate unique qualification evidence here before deleting the branch unless the exact evidence is already preserved on `main` or an archive tag is explicitly sufficient.
