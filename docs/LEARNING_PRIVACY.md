# Learning privacy and data safety

Private Learning V0.1 uses explicit collection and deny-by-default boundaries:

- Owner feedback or a reviewed manual import is eligible; Public Training is OFF.
- Secret Firewall runs before content persistence. Blocked or warned secret-like content stores only rejection metadata and no prompt/response reference.
- Email, phone, identity-card, passport, bank-account, and full-address patterns are redacted before content storage.
- Model output is synthetic data. It is not ground truth unless the Owner approves it, deterministic validation succeeds, or a verified business outcome supports it.
- Runtime content is bounded per item and in total, content-addressed, mode `0600`, path-confined, and symlink-resistant.
- Events contain bounded metadata, hashes, labels, counts, and decisions—not raw conversations or credentials.
- Imports accept bounded JSONL only from `runtime/learning/imports`; exports go only to `runtime/learning/exports`. Traversal, symlinks, archives, unknown fields, and oversized files are rejected.

Deletion marks a candidate and its linked preference pairs unavailable for all future dataset builds. Unreferenced raw content may then be removed. Existing adapters may still encode learned effects; true forgetting therefore requires excluding the data and retraining/replacing the adapter. The UI states this limitation explicitly.

V0.1 retention is report-only. Cleanup produces a dry-run candidate list and never purges automatically. Any future destructive cleanup must be reference-aware and separately approved.
