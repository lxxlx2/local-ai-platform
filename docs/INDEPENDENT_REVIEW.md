# Independent Review

Mandatory lifecycle: Producer → deterministic validation → self acceptance → independent review → security gate → acceptance ready → user UX acceptance.

The Producer cannot set `REVIEW_PASSED` for its own candidate. A Reviewer is read-only: it reports PASS/FAIL/findings/evidence/severity/missing tests/recommendations, but cannot edit, commit, push, deploy, or close findings. Findings close only after an independent re-review of a later candidate passes.

Use Codex `/review` on a commit or diff. Prefer Detached delivery; otherwise use a separate read-only reviewer context with only requirements, candidate diff, tests, security constraints, and relevant sources. Auto-review for approvals is a security mechanism, not a quality review.
