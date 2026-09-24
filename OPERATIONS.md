# Operations

## Routine commands

```bash
uv run apex-weather health
uv run apex-weather discover
uv run apex-weather run-once
uv run apex-weather readiness
scripts/status.sh
scripts/logs.sh
scripts/backup.sh
```

Paper mode is healthy when cycles complete, weather inputs remain fresh, SQLite
integrity is `ok`, and `SKIP` reasons reflect real gates. A zero-trade day is not
an incident.

## Incident behavior

- Weather/API outage: fail closed, record a health event, retry next cycle.
- Database integrity failure or low disk: stop execution and restore/repair.
- Clock drift: verify `timedatectl status`; the host must have synchronized NTP.
- Restart: systemd restarts automatically. Paper state is loaded from SQLite.
- Rule/source change: affected specs become blocked until parser/registry review.
- Emergency stop: `sudo systemctl stop apex-weather`.

## Data and backups

Persistent state is `/var/lib/apex-weather`. Backups use SQLite's online backup
command and must be copied off-host periodically. Do not copy a live WAL database
with a plain file copy. Deployment never replaces the data directory.

## Logs

The service writes structured lines to journald. `scripts/logs.sh` follows them;
journald rotation/retention is configured at the host level. Secrets and Kalshi
auth headers must never appear in logs.

The daemon stores a heartbeat after every successful cycle. Health fails if an
existing heartbeat is older than five minutes. Little Lio sends the `/today`
summary at `TELEGRAM_DAILY_SUMMARY_UTC` and forwards only new error/critical
health events; warnings remain queryable without generating noisy alerts.

## Live control

`uv run apex-weather enable-live --confirm I_ACCEPT_LIVE_RISK` refuses unless the
promotion gate passes. It only records enablement; switching `TRADING_MODE=live`
is separate. Live startup reconciles Kalshi state before submitting orders; keep
paper mode until the evidence gate and a code/operations review both pass.
