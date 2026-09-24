# Live Readiness

## Recommendation: DO NOT ENABLE LIVE TRADING

The deterministic core and paper/research pipeline are implemented, but this
checkout has no accumulated calibration or realized paper sample. The required
minimum is currently 5,000 market snapshots and 200 resolved markets, plus Brier
score at or below 0.20, positive net EV, positive realized paper P&L, drawdown at
or below 15%, zero unresolved parser failures, and zero major data incidents.

Run `uv run apex-weather readiness`. The command exits non-zero until every gate
passes. Even after it passes, enabling live requires the exact confirmation
phrase and a separate `TRADING_MODE=live` change. On live startup the daemon
reconciles remote Kalshi orders and positions before it can submit a new order.

The anecdotal $100-to-$50,000 outcome is not evidence and is excluded from the
baseline or projection logic.
