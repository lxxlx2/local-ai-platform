# Golden evaluation policy

Every adapter is compared with the unchanged base model on the same immutable Golden Holdout. Scores must provide the exact dimension set for the namespace; missing or extra dimensions are rejected.

General dimensions: instruction following, format correctness, factual consistency, security compliance, code quality, project behavior, and business-task quality.

X-content dimensions: hook quality, clarity, factual discipline, no invented source, platform fit, conciseness, CTA quality, and safety.

Promotion is denied for any critical regression in security, code, safety, or factual discipline; a catastrophic per-dimension drop; inadequate overall improvement; or inadequate business improvement. Eval runs and per-dimension results are persisted. A promotion must bind to the exact PASS eval run, adapter ID, and score; an in-memory or transplanted result is insufficient.

No model-as-judge output is trusted by default. V0.1 accepts deterministic fixture or reviewed human scores. Golden examples never become training examples.
