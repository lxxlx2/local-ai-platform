# MySQL network review

## CURRENT_STATE

Homebrew MySQL 8.4 is running as the current user from `/opt/homebrew/opt/mysql@8.4/bin/mysqld`, using `/opt/homebrew/var/mysql`. It listens on TCP `*:3306` and `*:33060`. No conventional configuration file was found at `/etc/my.cnf`, `/etc/mysql/my.cnf`, `/opt/homebrew/etc/my.cnf`, or `~/.my.cnf`.

## RISK

Wildcard listeners make MySQL reachable from local network interfaces when network policy permits. macOS Application Firewall is currently disabled. The AI service must not inherit this exposure.

## RECOMMENDATION

Do not change MySQL in V1. Before any future MySQL bind-address or firewall change, identify dependent clients and take a separately approved, reversible maintenance action. Do not expose MySQL through Tailscale unless there is a specific authenticated use case.

## PROPOSED_FIX

Future only: determine actual clients, then bind MySQL to loopback or an explicitly required private interface and enforce a firewall policy.

## ROLLBACK

Restore the prior MySQL bind configuration and service state after verifying local access; never delete its data directory.
