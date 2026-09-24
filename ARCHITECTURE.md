# Architecture

## Decision path

`market rules → SettlementSpec → observations + forecasts → Monte Carlo →`
`executable orderbook + fees → risk checks → decision → paper execution`

Every arrow persists evidence to SQLite. A failure before execution becomes a
documented `SKIP`; it is never converted into a guessed default.

## Boundaries

- Kalshi public market data is separate from authenticated portfolio/order APIs.
- Raw weather reports and normalized values remain distinguishable in storage.
- A settlement spec describes one market, including source, station, thresholds,
  inclusivity, observation window, timing and ambiguity flags.
- The probability engine is deterministic for a fixed seed and never calls AI.
- EV uses opposite-side bids to derive executable asks and walks depth.
- Risk caps apply after sizing. Poor calibration reduces Kelly sizing.
- Jev uses typed answers, a strict schema, hard daily/monthly budgets, and can only
  add veto codes. High-edge deterministic opportunities bypass it.

## Persistence and recovery

SQLite runs in WAL mode with foreign keys and idempotent migrations. Orders use
unique client order IDs. The daemon is stateless beyond cache: durable facts live
in SQLite or, for live portfolio truth, Kalshi. The live executor reconciles
remote orders/positions on startup and blocks on any unknown remote cost basis.

## Runtime

One Python process is supervised by systemd. Market rules refresh every ten
minutes, ensemble forecasts cache for three hours, observations cache for thirty
seconds, and polling rises from 60 seconds to 15 seconds near cadence minutes
learned from actual station reports.
