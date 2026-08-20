# Approval security

Approvals and task state transitions are deterministic SQLite records in the private plane. Owner approval requires the authenticated Owner identity and an expected version; replays return an already-processed result and cannot cause duplicate side effects. Demonstration approvals never access business repositories, Git, publishing, or model control.
