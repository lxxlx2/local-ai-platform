# Service management

No persistent service is enabled until API, 30-minute stability, memory/swap, thermal, Codex compatibility, and Tailscale checks pass. The eventual service must use the upstream-supported lifecycle method, be easy to inspect and stop, log locally, bind localhost by default, and have a documented rollback. No unmanaged shell daemon is permitted.
