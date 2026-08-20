# Data isolation

Local development uses physically separate SQLite files: private control/memory data under `runtime/control-plane/`, and public data under `runtime/public-ai/`. They are runtime data and Git-ignored. All repository reads additionally enforce the authenticated internal user scope; IDs alone never grant access.

The production target is separate PostgreSQL databases (or independently credentialed schemas) via `PRIVATE_DATABASE_URL` and `PUBLIC_DATABASE_URL`. Public application code must not receive a private connection. Git stores code and migrations only, never conversations, files, databases, memories, caches, logs, or credentials.
