# Dataset lifecycle

1. Capture an Owner-private candidate after Secret Firewall and privacy filtering.
2. Record explicit status: pending, rejected, approved, redacted, dataset-assigned, or expired.
3. Represent corrections as an approved chosen response plus rejected response in a preference pair.
4. Build inside one namespace with canonical normalization and content-hash deduplication.
5. Assign train/validation/test deterministically from a fixed seed and content hash.
6. Assign Golden Holdout only through an explicit candidate list.
7. Persist an immutable version, examples, and manifest with provenance hashes.

Golden isolation is permanent across versions: a historical non-holdout example cannot later become Golden, and a Golden example cannot later enter train/validation/test. Dataset tables are protected by SQLite update/delete triggers. A new build always creates a new namespace version.

Supported records are normalized chat/SFT JSONL and preference JSONL. Serialization is UTF-8, size-bounded, and rechecked by Secret Firewall. Raw JSONL and generated adapter weights remain runtime artifacts and must not enter Git.
