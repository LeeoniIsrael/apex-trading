"""Prospective, event-level evaluation of the current weather strategy.

The first qualifying decision for each station/day is fixed before its outcome.
Sibling temperature contracts and repeated polling never count as independent
successes. Hypothetical returns assume an immediate fill at the saved ask;
actual fills may differ or never occur.
"""

from __future__ import annotations

import math
from statistics import mean, stdev

from src.research.experiments import MODEL_VERSION

# Frozen before prospective collection begins. Historical results suggested
# this hypothesis; they must not be reused as its validation sample.
PRICED_LAG_START = '2026-10-13T00:00:00+00:00'


def _settled_signals(database, model_version: str, condition: str):
    with database.connect() as c:
        return c.execute(
            "SELECT r.event_key,r.split,r.captured_at,r.side,r.price_cents,"
            "r.contracts,r.fee_usd,r.probability,s.yes_outcome,s.settled_at "
            "FROM research_candidates r JOIN settlements s ON s.ticker=r.ticker "
            f"WHERE r.model_version=? AND {condition} "
            "AND s.final=1 AND r.id IN ("
            "SELECT MIN(r.id) FROM research_candidates r WHERE r.model_version=? "
            f"AND {condition} GROUP BY event_key) "
            "ORDER BY r.event_key", (model_version, model_version),
        ).fetchall()


def _summarize(rows) -> dict:
    groups: dict[str, list[tuple[float, float]]] = {'development': [], 'holdout': []}
    for row in rows:
        if row['captured_at'] >= row['settled_at'] or row['split'] not in groups:
            continue
        probability, price, fee = row['probability'], row['price_cents']/100, row['fee_usd']
        if (not all(math.isfinite(v) for v in (probability, price, fee))
                or not 0 <= probability <= 1 or not 0 < price < 1
                or not 0 <= fee or row['contracts'] <= 0):
            continue
        won = int(row['yes_outcome'] == (1 if row['side'] == 'yes' else 0))
        pnl = row['contracts']*(won-price)-fee
        groups[row['split']].append((probability-won, pnl))

    summary = {}
    for split, samples in groups.items():
        returns = [pnl for _, pnl in samples]
        n = len(samples)
        lower = (mean(returns)-1.96*stdev(returns)/math.sqrt(n)) if n >= 30 else None
        summary[split] = {
            'independent_events': n,
            'brier_score': round(mean(error*error for error, _ in samples), 4) if n else None,
            'hypothetical_after_fee_pnl_usd': round(sum(returns), 2),
            'mean_return_lower_bound_usd': round(lower, 4) if lower is not None else None,
            'profitable_evidence': bool(split == 'holdout' and n >= 200
                                        and lower is not None and lower > 0),
        }
    return summary


def evaluate_strategy(database, model_version: str = MODEL_VERSION) -> dict:
    from src.research.consensus_no import LEGACY_VERSION, VERSION, START
    with database.connect() as c:
        consensus_rows = c.execute(
            'SELECT r.*,s.yes_outcome,s.settled_at FROM shadow_signals r '
            'JOIN settlements s ON s.ticker=r.ticker WHERE r.strategy_version=? '
            'AND s.final=1',(VERSION,)).fetchall()
        legacy_consensus_rows = c.execute(
            'SELECT r.*,s.yes_outcome,s.settled_at FROM shadow_signals r '
            'JOIN settlements s ON s.ticker=r.ticker WHERE r.strategy_version=? '
            'AND s.final=1',(LEGACY_VERSION,)).fetchall()
    qualified = _summarize(_settled_signals(
        database, model_version, "baseline_action LIKE 'BUY%'"))
    lag = _summarize(_settled_signals(
        database, model_version, 'lag_candidate=1'))
    priced_lag = _summarize(_settled_signals(
        database, model_version,
        "lag_candidate=1 AND observation_age BETWEEN 0 AND 300 "
        "AND net_ev_usd>0 AND spread_cents BETWEEN 0 AND 8 "
        "AND forecast_disagreement_f BETWEEN 0 AND 10 "
        "AND r.source='weather_company' "
        f"AND captured_at>='{PRICED_LAG_START}'"))
    return {'model_version': model_version, 'development': qualified['development'],
            'holdout': qualified['holdout'], 'official_reading_lag': lag,
            'fresh_consensus_no_v2': {'starts_at':START, **_summarize(consensus_rows),
                                     'note':'Paper shadow only. No order path; assumes saved ask fills.'},
            'fresh_consensus_no_v1': {
                **_summarize(legacy_consensus_rows),
                'note': 'Retired after its first prospective signal revealed an unsupported model-market disagreement. Historical paper rows remain for audit only.',
            },
            'priced_lag_shadow_v1': {
                'starts_at': PRICED_LAG_START, **priced_lag,
                'note': 'Prospective exploratory arm: positive modeled after-fee EV, spread <=8c, fresh official reading and forecast agreement. Other live guards still apply; this arm never authorizes orders.',
            },
            'note': 'One pre-settlement signal per station/day. The lag arm is exploratory; hypothetical fills are not real profit.'}
