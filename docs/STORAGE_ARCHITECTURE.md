# Storage architecture

PostgreSQL plus pgvector is the production target for users, sessions, messages, summaries, memories, embeddings, usage, and public tasks. The repository includes a configuration-ready PostgreSQL/pgvector migration surface but no remote provider is configured.

Large media belongs in future S3-compatible object storage. `LocalObjectStorage` is only a development adapter. `S3CompatibleObjectStorage` refuses operation without a supplied client and bucket. No provider, account, bucket, or credentials are created in this phase.

The Mac is a bounded cache/spool, not an unlimited history store. Defaults: 2 GiB local cache and 512 MiB public spool. Cleanup is limited to explicitly synced public cache/output records; private unsynced data, models, repos, secrets, and Git are never cleanup candidates.
