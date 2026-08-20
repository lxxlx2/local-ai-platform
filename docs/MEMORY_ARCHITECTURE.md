# Memory architecture

Prompt assembly is bounded: current turn + a recent window (12 messages / roughly 3K tokens) + bounded conversation summary + scoped relevant memories. It never sends every historical message to the model. Memory records have user scope, category, subject, content, source reference, confidence, timestamps, status, and soft deletion.

Owner memory supports preferences, rules, project memory, conversation facts, and task context. Public long-term memory defaults off and requires opt-in. Semantic retrieval is architecture-ready with a deterministic test embedding provider; a real embedding model is not installed, so production vector search remains `PROVIDER_PENDING`.

Canonical assistant responses are persisted before presentation chunking. Individual Telegram chunks are never stored as separate assistant messages; later recent-context retrieval reads the complete canonical response.
