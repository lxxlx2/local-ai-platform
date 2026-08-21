# Public AI security

Public users receive a distinct menu and a distinct data store. They cannot access Owner projects, approvals, system status, model management, Git, shell, private files, private memory, or credentials. The application derives identity only from Telegram user ID, not username, message claims, prompts, or model output. Forged owner callbacks are denied by backend authorization, not merely hidden in the UI.

Public chat has no tools. Arbitrary URL downloading is disabled. Long-term memory is opt-in. The bot warns that Telegram is not a secret vault and blocks high-confidence secrets before model calls, persistence, logging, summaries, or embeddings.

Public and Owner ordinary chat share only the presentation renderer and chunker. Their identity scopes, repositories, memories, task access, and authorization remain separate.

Workflow Supervisor state is an Owner-private runtime database. Public menus expose no entry, and forged `supervisor:*` callbacks are rejected by the Owner authorization gate before any job lookup or mutation. V0.1 accepts only a fixed safe demo from Telegram; arbitrary prompt-to-agent execution is not available.
