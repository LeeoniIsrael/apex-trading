# APEX weather strategy and evidence policy

APEX looks for a narrow mismatch between the probability of a weather outcome
and the price at which a contract can actually be bought. It uses the specific
contract's settlement rule, the named official source, fresh observations,
independent forecasts, measured forecast error when available, and the full
entry fee. It bets only when the estimated advantage survives uncertainty,
liquidity, spread, and account limits. This is a hypothesis, not demonstrated
profit.

The operator's `:53` METAR observation idea is useful as a **timing hypothesis**:
new official observations may reach the weather feed before a thin market fully
reprices. It is not a settlement rule. A day's final high or low can differ from
any one hourly reading. Kalshi's [weather guidance](https://help.kalshi.com/en/articles/13823837-weather-markets)
distinguishes daily NWS climate reports from hourly Weather Company observations
and says to follow each market's actual rules. The currently collected APEX
daily markets explicitly name The Weather Company, so APEX parses those exact
rules and uses its named feed. Source changes or ambiguity are hard skips.

The low-cost autonomous loop is:

1. Collect contract rules, official weather observations, independent forecasts,
   executable order books, and fee schedules. Store their source and timestamp.
2. Recompute a settlement-aware probability with forecast and preliminary-data
   uncertainty. Reject stale, conflicting, or unsupported inputs.
3. Calculate after-fee expected value at the executable price. Apply source,
   spread, exposure, cash, daily-loss, and stop checks before any order.
4. Save research snapshots on a new weather reading, quote, fee, or decision, or
   every 15 minutes when unchanged. Repeated polling is not independent data.
   Full order books and probability records follow a 15-minute cadence unless
   weather or the model changes; quote changes within five minutes of a new
   official reading are kept for the lag study. Repeated skipped decisions are
   sampled, while every actionable buy retains its full source evidence.
5. When official outcomes arrive, score the first pre-settlement qualifying
   opportunity for each station-day. Keep development and holdout results
   separate; show sample size, Brier score, and fee-adjusted hypothetical return.
   Do not count sibling temperature ranges as separate wins. Track the
   official-reading repricing-lag hypothesis as a separate exploratory arm;
   it does not authorize real orders.
6. Use settled **development** events to propose model revisions. Freeze a new
   version before examining its holdout results. Never promote a model or
   resume live trading automatically from a favorable small sample.

A trading pause leaves this read-only collection and evaluation loop running.
It blocks every order, and it also suppresses optional paid AI reviews. A
resume partway through a cycle cannot turn that research-only cycle into a
trade; the following cycle must pass the controls again.

At the time of this change, the current model has 42 observed station-day
events, no qualifying settled holdout opportunities, and no new real bets. Its
high research-row count reflects frequent polling, not independent evidence.
The prior three real bets lost about $2.69 after trading fees. These facts do
not establish a profitable strategy. The latest real account held about $95.32
cash with no open positions, and trading remained paused after a server failure.

The automated report is written to `launch-readiness.json` by the existing
worker at most every ten minutes. `strategy_evaluation` is read-only and never
authorizes a trade. A positive hypothetical return is not an actual fill or
profit; deployment and real-money resumption remain separately controlled.

On October 2 the research database reached roughly 7.2 GB, including more
than two million predictions and order-book records and 1.8 million decision
records. The sampling change limits future duplicate growth without deleting
historical records or changing bet selection. Disk space remains monitored.

On October 5, forecast-error scoring was found to scan the entire multi-million
row weather-forecast table for each saved forecast run. An index on forecast-run
ID and valid time makes those lookups local. This changes research-cycle
performance only; it does not change forecasts, model thresholds, or orders.
