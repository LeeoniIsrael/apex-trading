# Deployment

No usable `hcloud` CLI, `HCLOUD_TOKEN`, or SSH host was present during this build,
so no server was created and the old server described in historical docs was not
assumed to exist.

## One-time Hetzner Console setup

1. Open the Hetzner project you want billed for APEX (create one named `APEX` if
   none exists).
2. Choose **Add Server**.
3. Location: **Ashburn, VA (`ash`)**.
4. Type: **CPX11**, the smallest listed Ashburn shared AMD option suitable here.
5. Image: **Ubuntu 24.04**.
6. Add your Mac's existing public SSH key, or create a dedicated Ed25519 key.
7. Networking: enable Primary IPv4 (and free IPv6 if desired).
8. Firewall: inbound TCP 22 only, preferably restricted to your current public
   IP; outbound unrestricted. No application port is required.
9. Name: `apex-weather-01`.
10. Give Codex the server IPv4, SSH username and local private-key path. Do not
    paste the private key or API tokens into chat or git.

As of 2026-09-23, CPX11 Ashburn plus IPv4 is approximately $21.09/month before
tax. Confirm the console price before creation.

## Automated alternative

Install `hcloud`, create a read/write project token in the Hetzner Console, and
export it only in your local shell as `HCLOUD_TOKEN`. Codex can then list existing
servers/firewalls/keys before creating anything. Do not save the token in this
repository.

## Host bootstrap and deploy

On the new host, create `/etc/apex-weather/apex-weather.env` from `.env.template`
and store any Kalshi PEM at `/etc/apex-weather/kalshi_private.pem` with mode 600.
Keep `TRADING_MODE=paper`.

From the repo on the Mac:

```bash
export APEX_HOST=<server-ip>
export APEX_SSH_KEY=<absolute-private-key-path>
scripts/deploy.sh
scripts/status.sh
scripts/logs.sh
```

The deploy script syncs code but excludes `.env`, keys, databases and git state.
It installs the systemd unit, restarts in paper mode, and leaves persistent data
under `/var/lib/apex-weather` untouched.

## Readiness and explicitly gated live preparation

Keep `TRADING_MODE=paper` throughout implementation and validation. Use:

```bash
apex-weather accounting
apex-weather research
apex-weather readiness
apex-weather balance-test
```

The balance test is read-only, validates an authenticated response and records
only a pass/fail check. It never prints credentials or account balances.
Balance units follow the [Kalshi balance API](https://docs.kalshi.com/api-reference/portfolio/get-balance).
Bid complements follow the [official orderbook documentation](https://docs.kalshi.com/getting_started/orderbook_responses).
Paper and live databases must differ; set `LIVE_DATABASE_PATH` explicitly.

The following command is **documentation only; it was not executed**:

```bash
apex-weather enable-live --confirm I_ACCEPT_LIVE_RISK
```

It refuses unless all readiness checks pass and a new authenticated balance
check clears the floor. It writes a mode-600 JSON marker bound to the configured
paper and live database paths. It does not change `TRADING_MODE`. Activation
requires a separate operator decision and environment change in a later turn.

**Current live blockers:** remote position/fill cost-basis reconciliation and
pagination are deliberately fail-closed; this adapter is not ready for unattended
real-money operation. Unknown remote positions, ambiguous POSTs, or pagination
stop trading. The operator must also validate and record fresh (<24 hours)
`idempotency`, `emergency_stop`, `telegram_alerts`, and `equity_reporting` evidence
in `verification_checks`. There is no chat/API shortcut for marking these passed.
Do not fabricate these records to enable trading. Historical health incidents
currently require investigation and prevent promotion; there is no automatic
waiver. Collect at least 200 prospective independent holdout events and 5,000
useful research snapshots; old repeated predictions do not satisfy these gates.

## Emergency stop and recovery

```bash
apex-weather emergency-stop
sudo systemctl stop apex-weather
```

This latches pause/stop in both databases and removes the live marker. It prevents
new submissions; it does not claim to cancel previously accepted remote orders.
Cancel outstanding orders in Kalshi, verify orders/positions read-only, reconcile
all fills and cost basis, investigate the cause, and rerun readiness before
manual local recovery. Never clear an ambiguous submission by issuing a new ID.
POST requests are not automatically retried. All attempted IDs remain reserved
across restarts, including failed or uncertain requests.
