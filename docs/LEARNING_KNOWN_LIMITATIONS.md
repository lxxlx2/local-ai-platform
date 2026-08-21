# Private Learning V0.1 known limitations

- Production MLX/LoRA training is disabled; the dedicated training venv is not configured.
- No adapter is automatically loaded, switched, or served by oMLX.
- Telegram V0.1 records GOOD, BAD, and SKIP. A richer correction editor for `BETTER_RESPONSE` remains future UI work; the engine and import path already support preference pairs.
- Business metrics are manually or fixture verified; no external analytics connector is active.
- S3-compatible storage is an interface skeleton only.
- Retention cleanup is dry-run only.
- Deleting source data prevents future training but does not erase effects from an already trained adapter.
- Independent review is not run in this phase by design; review status remains PENDING until a later authorized review.
