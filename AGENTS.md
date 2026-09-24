# APEX Weather Agent Guide

## Mission

Build and operate a weather-only Kalshi research and trading service. The
default is paper mode with a $100 experimental bankroll. No component may
promise returns or lower thresholds merely to create trades.

## Non-negotiable safety rules

- Parse the rules of every market. Never infer settlement from a title or
  series ticker alone.
- A missing station, source, observation window, threshold, or rule detail is
  a hard `SKIP`.
- The live path requires both `TRADING_MODE=live` and a persisted one-time
  enablement record. Never promote automatically.
- Deterministic weather, price, fee, and risk calculations make the decision.
  Jev or an LLM may veto but may not override a hard block.
- Kalshi orderbooks contain YES and NO bids. Derive the opposite-side ask as
  `1 - best opposing bid`; never treat a bid as an executable buy price.
- Never log auth headers, signatures, private keys, tokens, or raw secrets.
- API/data failures fail closed. Financial and settlement paths may not use
  broad `except: pass` handling.
- Preserve raw observations separately from normalized observations and store
  all timestamps in UTC plus the market/station local representation.
- Daily NWS climate windows use local standard time where the rules say so.
  Do not substitute ordinary wall-clock midnight during DST.

## Development workflow

1. Work on `weather-autonomous-v2`, not `main`.
2. Read the relevant module and tests before editing.
3. Keep pure settlement, probability, fee, EV, and sizing logic deterministic.
4. Add focused tests for every financial or time-boundary change.
5. Run `uv run pytest -q` before each commit.
6. Keep commits incremental and do not deploy or enable live trading as part
   of ordinary development.

## Runtime shape

- One Python service on Ubuntu 24.04 under systemd.
- SQLite in WAL mode is the durable research and trading store.
- Free authoritative weather sources and local caching are preferred.
- Runtime AI is disabled by default and has daily/monthly spend caps.
- Persistent data and secrets are outside the git checkout and survive deploys.

## Current source-of-truth warning

Kalshi has changed weather settlement sources over time. Some current daily
markets name The Weather Company while other contracts may still name NWS.
The individual market rules are authoritative, not this file or old docs.
