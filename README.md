# APEX Weather v2

APEX is a fail-closed Kalshi weather-market research and autonomous paper-trading
system. It discovers current temperature contracts, parses each contract's real
settlement rules, ingests official and preliminary weather data, simulates the
remaining weather day, prices trades from executable orderbook depth, and records
the full research trail in SQLite.

The default bankroll is **$100** and the default mode is **paper**. The software
does not assume a daily trade or any level of profitability. Expected monthly
income is **INSUFFICIENT DATA** until the readiness gate has a meaningful sample.

## Safety status

- Live execution is disabled by default; the daemon can reach it only after the
  evidence gate, persisted enablement record, live mode, and valid credentials.
- Unknown stations, conflicting rule sources, missing thresholds, stale data, and
  missing liquidity produce `SKIP`.
- Current Kalshi daily contracts may name either NWS or The Weather Company.
  The individual rules—not the title or series—control.
- Jev and GPT-6 Luna structured exception review are implemented but disabled.
  If configured, both are budgeted, receive no secrets, and may veto only.

## Quick start

```bash
uv sync --frozen
cp .env.template .env
uv run apex-weather init-db
uv run apex-weather discover
uv run apex-weather run-once
uv run apex-weather readiness
uv run pytest -q
```

`discover` and weather data collection use public endpoints and need no Kalshi
credentials. Keep `.env`, Telegram tokens, and Kalshi private keys outside git.

## Components

- `src/kalshi/`: hardened authentication, REST transport, orderbook and fees
- `src/weather/`: station registry, NWS/METAR/TWC/Open-Meteo providers
- `src/strategy/`: settlement, Monte Carlo, EV and decision rules
- `src/risk/`: sizing and portfolio limits
- `src/execution/`: realistic paper fills
- `src/storage/`: SQLite/WAL schema and migrations
- `src/research/`: collector, calibration and promotion gate
- `src/monitoring/`: health and cost accounting

See [ARCHITECTURE.md](ARCHITECTURE.md), [WEATHER_STRATEGY.md](WEATHER_STRATEGY.md),
[SETTLEMENT_RULES.md](SETTLEMENT_RULES.md), [OPERATIONS.md](OPERATIONS.md), and
[DEPLOYMENT.md](DEPLOYMENT.md).

## Current validation

The initial public-market smoke test on 2026-09-23 found hundreds of open
temperature contracts. The station registry now covers all 39 domestic stations
listed by the official weather.com/kalshi primary catalog; international and new
formats remain blocked until independently verified.

This project is experimental trading software, not a promise of profit.
