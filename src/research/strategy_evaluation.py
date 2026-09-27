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


def evaluate_strategy(database, model_version: str = MODEL_VERSION) -> dict:
    with database.connect() as c:
        rows = c.execute(
            "SELECT r.event_key,r.split,r.captured_at,r.side,r.price_cents,"
            "r.contracts,r.fee_usd,r.probability,s.yes_outcome,s.settled_at "
            "FROM research_candidates r JOIN settlements s ON s.ticker=r.ticker "
            "WHERE r.model_version=? AND r.baseline_action LIKE 'BUY%' "
            "AND s.final=1 AND r.id IN ("
            "SELECT MIN(id) FROM research_candidates WHERE model_version=? "
            "AND baseline_action LIKE 'BUY%' GROUP BY event_key) "
            "ORDER BY r.event_key", (model_version, model_version),
        ).fetchall()

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
    return {'model_version': model_version, 'development': summary['development'],
            'holdout': summary['holdout'],
            'note': 'One pre-settlement signal per station/day; hypothetical fills are not real profit.'}
