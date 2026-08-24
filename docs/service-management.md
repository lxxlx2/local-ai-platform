# Service management

No persistent service is enabled until API, 30-minute stability, memory/swap, thermal, Codex compatibility, and Tailscale checks pass. The eventual service must use the upstream-supported lifecycle method, be easy to inspect and stop, log locally, bind localhost by default, and have a documented rollback. No unmanaged shell daemon is permitted.

Workflow Supervisor V0.1 is an experimental exception only after explicit manual start: it uses checked-in start/stop/status scripts, an exact PID identity check, a leased SQLite singleton lock, bounded rotating logs, and graceful `SIGTERM`. It is not installed as launchd and is not started or deployed by the feature-branch build. See `WORKFLOW_SUPERVISOR.md`.

The model-download manager is another explicitly manual, finite background task rather than a persistent service. Its checked-in start/status/watch/stop scripts use a private identity, a singleton lock, exact owned child identities, and graceful pause/resume semantics. The watch command is read-only; Ctrl+C exits only the foreground display. No launchd or scheduled job is used.
