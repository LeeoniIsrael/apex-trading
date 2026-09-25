# Weather Strategy

## Model

Observed extremes are irreversible: a daily high can only rise and a daily low
can only fall. For the unobserved remainder, APEX samples ensemble-member paths,
station bias and forecast error, applies the settlement observation window, and
maps each simulated final extreme into the contract's exact inclusive/exclusive
range. The default is 10,000 simulations.

Raw ensemble fractions are shrunk toward 50% until empirical station calibration
exists. Predictions store confidence intervals, entropy, input age, current
extreme and eventual result for Brier/log-loss analysis.

## Data priority

1. The source named in the individual Kalshi rule (NWS CLI or weather.com/kalshi)
2. The Weather Company hourly readings when that source controls
3. METAR/ASOS and NWS station observations as explicitly preliminary evidence
4. NWS hourly forecasts
5. Open-Meteo GFS ensemble at the settlement station coordinates

No generic downtown coordinate or consumer weather app is settlement truth.

## Trading

APEX compares model probability with the volume-weighted executable ask—not the
midpoint—then subtracts configured Kalshi fees and slippage. It skips on negative
net EV, excessive spread, inadequate depth, stale/conflicting data or risk caps.
There is no minimum daily trade count.

The 1–2¢ hypothesis is measured by price bucket in the research database. Cheap
contracts receive neither a blanket ban nor a lottery-style override.

## Jev and AI

`JEV_ENABLED=false` and `RUNTIME_LLM_ENABLED=false` by default. When configured,
Jev receives compact non-secret state only after deterministic settlement, data,
EV and risk checks pass. Its typed response is a veto; malformed, uncertain or
over-budget responses become `SKIP`. Obvious edges bypass Jev. Calls, latency,
cost, entry price, fill expectation and final action are persisted in
`decision_benchmarks` for deterministic-only versus deterministic-plus-Jev
evaluation. AI never supplies the weather probability or overrides hard blocks.

The optional runtime exception reviewer uses GPT-6 Luna structured outputs only
inside a configured forecast-disagreement band. It receives compact, non-secret
diagnostics—not credentials or private keys—and can only add veto codes. API
failure, schema failure and exhausted shared AI budgets all fail closed.

## Version 1 prospective experiments

`research_candidates` records executable price, depth, spread, side, fee-adjusted
edge, observation age, forecast surprise and disagreement, city/station/source,
time stratum, Jev action, control assignment, linked order and worst observed bid.
Join its ticker to `settlements` for the official outcome; `apex-weather accounting`
replays actual fills to report each entry order's closed quantity and realized P&L.
Maximum observed adverse movement is `MAX(0, price_cents - min_bid_cents)`;
missing observations are unknown, not evidence of no adverse movement.

The fixed `apex-experiment-v1` event hash assigns 20% to holdout and a disjoint
10% to no-trade control. A city/station/source/day stays together, including
sibling temperature ranges and high/low contracts. Readiness takes the first
qualifying candidate per event; arm reports use the first per ticker and are
not independent-event significance tests. Never tune against holdout results.
Freeze a new experiment/model version and collect new future data when changing
thresholds. Existing historical predictions are not retroactively holdout data.

`apex-weather research` compares baseline, fresh-observation lag (observation
under five minutes old, probability change at least five points, smaller price
change), early/midday/near-close entry, price buckets, separate sources, fixed
no-trade control and Jev veto outcomes. These are observational comparisons,
not proof of causality. Hypothetical P&L assumes available quoted depth; it is
reported separately from actual fills. No thresholds were loosened.

Paper trading now holds positions to official settlement. The former early-exit
simulation assumed unlimited bid depth and could charge historical fees again;
it is disabled pending a depth-aware FIFO exit implementation. Missing or stale
bids have zero liquidation value in conservative equity reporting. Starting
bankroll remains the risk ceiling even if paper equity increases.
