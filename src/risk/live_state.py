"""Cycle risk inputs come only from reconciled real-account state.

Cash drawdown treats open positions as worth zero, matching the executor's
conservative limits. It is not a reported mark-to-market profit calculation.
"""
from dataclasses import dataclass
import json
import math

from src.strategy.settlement import parse_settlement_spec


@dataclass(frozen=True)
class LiveCycleState:
    cash: float
    exposure: float
    cities: dict[str, float]
    tickers: frozenset[str]
    daily_cash_change: float
    drawdown: float


def live_cycle_state(executor, research_database, now):
    audit = executor.audit
    cash, exposure = float(audit.cash), float(audit.open_cost)
    with executor.database.connect() as connection:
        controls = dict(connection.execute("SELECT key,value FROM control_state WHERE key LIKE 'live_%'"))
    peak = max(cash, float(controls.get('live_peak', cash)))
    day_start = float(controls.get('live_day_'+now.date().isoformat(), cash))
    if any(not math.isfinite(v) or v < 0 for v in (cash, exposure, peak, day_start)):
        raise RuntimeError('invalid live account risk state')
    cities = {}
    for ticker, position in audit.positions.items():
        with research_database.connect() as connection:
            row = connection.execute('SELECT raw_json FROM markets WHERE ticker=?', (ticker,)).fetchone()
        if row is None:
            raise RuntimeError('missing held-market rules for live exposure')
        spec = parse_settlement_spec(json.loads(row['raw_json']))
        if not spec.tradeable or not spec.city:
            raise RuntimeError('ambiguous held-market rules for live exposure')
        cost = float(position['cost'])
        if not math.isfinite(cost) or cost < 0:
            raise RuntimeError('invalid live position cost')
        cities[spec.city] = cities.get(spec.city, 0.0)+cost
    if not math.isclose(sum(cities.values()), exposure, abs_tol=.0001):
        raise RuntimeError('live exposure does not match audited positions')
    return LiveCycleState(cash, exposure, cities, frozenset(audit.positions),
                          cash-day_start, (peak-cash)/peak if peak else 0.0)
