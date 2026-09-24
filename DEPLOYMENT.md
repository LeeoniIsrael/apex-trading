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
