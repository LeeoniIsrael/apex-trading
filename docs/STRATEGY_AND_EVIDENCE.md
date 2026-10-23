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

On October 6, a shadow station-bias correction was checked against 87
independent Weather Company holdout outcomes. The predeclared correction used
at least eight prior development outcomes per station and high/low type, shrank
the mean error by `n/(n+20)`, and capped it at 2°F. Requiring each training
outcome to have settled before the evaluated forecast left only seven corrected
holdout cases. Overall mean absolute error changed from 3.203°F to 3.190°F;
high-temperature error worsened slightly. This is too little prospective
evidence to alter live probabilities or loosen any trading guard. Earlier
comparisons that allowed later outcomes into earlier evaluations overstated
the apparent improvement.

On October 12, 11 of the last 21 recorded fresh-reading lag signals had
nonpositive modeled after-fee EV, and nine exceeded the 8-cent spread guard
(these groups can overlap). The broad exploratory arm remains unchanged.
A separate `priced_lag_shadow_v1` hypothesis is frozen before collection starts
at 2026-10-13 00:00 UTC: a Weather Company reading aged 0–300 seconds, positive
modeled after-fee EV, spread at most 8 cents, and forecast-family disagreement
at most 10°F. It scores the first qualifying signal per station-day, with
development and holdout kept separate. Older rows cannot validate this new
hypothesis. Model-confidence and all other live guards still apply to orders;
the shadow report grants no trading permission and its hypothetical fills
do not establish actual profit.

## Fresh consensus NO paper strategy — version 1 retired October 23

The nine settled priced-lag examples inspected on October 20 all bought YES
and lost, often just outside a narrow range. A 3°F already-crossed-boundary NO
test produced zero development opportunities at the available prices, so it
was not deployed. The alternative `fresh-consensus-no-v1` tests saved best-side
NO opportunities with these fixed gates: named Weather Company daily market,
reading at most 30 minutes old, both forecasts fresh and their remaining
extremes within 3°F, at most six hours to close, modeled NO probability at least
90%, ask at most 85 cents, spread at most 8 cents, and at least five cents per
contract of estimated value after fees using a fixed 90% probability ceiling.
The fee-inclusive position cost cannot exceed $2. The 90% ceiling is an
uncalibrated stress assumption, not a promised win rate.

The replay selects the first eligible station-day signal before joining final
outcomes, uses only forecast runs generated and ingested before the signal,
and uses saved executable asks and fees. It found eight settled development
events, seven wins and one loss, totaling **+$1.00 hypothetically after fees**.
There were **zero qualifying holdout events**. Historical rules are taken from
the saved market record; this is a diagnostic replay, not proof of attainable
fills or profit. The previously inspected holdout is not a fresh validation set.
No strategy threshold was changed after that holdout query.

Prospective collection started at **2026-10-21 00:00 UTC**. Its first settled
development signal lost **-$1.99 hypothetically after fees**: it bought NO at
2 cents against a 98-cent YES market while the model's capped NO estimate was
90%. The final Weather Company minimum was 54°F, inside the 53--54°F YES bin.
That one loss does not prove a general result, but the 88-point price conflict
shows the model had no evidence strong enough to take the other side. Version
1 is retired and stays in the report as an audit record; it will not collect
more signals.

Version 2 begins at **2026-10-23 16:00 UTC**. It preserves every version 1
gate and adds a fixed 30-point model-versus-market disagreement guard. A larger
gap is allowed only when a preliminary official daily extreme has already made
the YES contract impossible (a low already below its required lower bound, or
a high already above its required upper bound). Forecasts and current
temperatures alone cannot override the market. This is a prospective paper
test, not a claim that version 2 is profitable.

A separate small
`shadow_signals` table keeps one opportunity per station-day plus the exact
rule, observed extreme, forecast extremes/timestamps, ask, quantity and fee.
The research service records it only while trading is blocked. The strategy
has no executor or account-control path; existing live guards, pause state,
$2 per-bet and $5 daily limits are preserved. The launch report publishes its
development and holdout results under `fresh_consensus_no_v1`. Reproduce the
historical study with `src.research.consensus_no.replay(database, split)`.
