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

## Accounting, research, and communication verification

Use `apex-weather accounting` for fill-based cash, fees, open cost, liquidation
equity, realized/after-cost result, drawdown, order-level results, and discrepancies.
Missing or >120-second-old bid data is valued at zero; this is a conservative
liquidation estimate, not a claim that open contracts are worthless. Equity history
preserves the worst observed drawdown. A discrepancy stops new paper orders and
live promotion. Reconcile existing records before clearing a discrepancy.

Telegram `/status` and `/today` display equity separately from starting bankroll.
AI conversational replies only choose status/positions/costs/unknown; numbers and
weather descriptions are deterministic database renderings. Unknown topics say
“I don't know.” Verified input/output model prices are required for AI routing.
No real API call is used in unit tests. The API call bounds output and durably
reserves its worst-case budget before sending; uncertain charges remain reserved,
following [OpenAI's spending-controller guidance](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api#check-the-budget-before-each-request).

Alerts are checked every five seconds and persisted after successful delivery.
This provides at-least-once delivery: a crash after Telegram accepts a message but
before SQLite records it can repeat the alert. Old unsent fills can appear once
when upgrading. Sending failures retain the event for retry. Actual end-to-end
Telegram delivery must be verified on the server before recording readiness.
