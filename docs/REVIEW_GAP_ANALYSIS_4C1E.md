# Phase 4C.1E review gap analysis

The prior 51-test and independent-review PASS did not equal real-client acceptance:

- **TEST GAP:** navigation tests verified button existence, not each detail page's final back callback. A global `home` callback therefore passed.
- **FINAL RENDER GAP:** tests reconstructed plain canonical text but did not require native Telegram code entities or inspect the final parse mode.
- **REAL CLIENT GAP:** the private-use cleanup character looked empty in source/capture but produced a visible timestamp bubble in Telegram. Final client behavior still needs the user's visual gate.
- **SEMANTIC GAP:** syntax/import checks could not detect a decorator reading a control keyword and forwarding it unchanged to a wrapped function.
- **RUNTIME VALIDATION GAP:** “complete/runnable” wording had no recorded validation level and no controlled success-path execution fixture.

Phase 4C.1E closes these gaps with a parent-route registry, deletable `/start` cleanup message, renderer-owned safe HTML, final-payload reconstruction tests, generic control-keyword flow analysis, explicit validation levels, and an audited Golden execution fixture. Independent reviewers must use the expanded checklist in `INDEPENDENT_REVIEW.md`.
