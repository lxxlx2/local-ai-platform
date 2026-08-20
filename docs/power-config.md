# Power configuration

## OLD_CONFIG

Captured 2026-08-20. AC power: `sleep=1`, `displaysleep=10`, `disksleep=10`, `womp=1`, `powernap=1`, `networkoversleep=0`, `tcpkeepalive=1`. Battery power remains `sleep=1`, `displaysleep=2`, and low-power mode enabled.

## NEW_CONFIG

Pending. V1 needs AC-only idle system sleep disabled while preserving display sleep and all battery settings.

## ROLLBACK_COMMAND

Pending the exact recorded command and post-change verification. Restore AC `sleep` to `1` only; do not alter battery settings.

## SERVER_MODE_USAGE

Use only while on AC, open lid, with adequate ventilation. Do not rely on closed-lid operation. Disable server mode before travel if desired.
