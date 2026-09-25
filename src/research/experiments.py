"""Predeclared observational arms; never tune on the holdout results.

Partition the whole city/station/source/day event, not sibling temperature ranges.
One first qualifying candidate per ticker is used for arm reports. Time strata
are observational, not randomized causal estimates. No-trade control uses a
predeclared 10% event hash allocation. Existing thresholds remain unchanged.
"""
import hashlib
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import math

from src.research.accounting import paper_account
from src.storage.database import Database


def allocation(event_key: str) -> tuple[str, bool]:
    number = int(hashlib.sha256(('apex-experiment-v1|' + event_key).encode()).hexdigest(), 16)
    return ('holdout' if number % 5 == 0 else 'development', number % 10 == 1)


def record_candidate(db: Database, *, spec, now, side, edge, baseline, final, jev,
                     observation_age, surprise, disagreement, probability_change,
                     book_change, latest_bid):
    event = '|'.join((spec.station_id or '', spec.official_source.value,
                      str(spec.market_date)))
    split, control = allocation(event)
    if spec.last_trading_time is None or spec.last_trading_time <= now:
        raise ValueError('market close time is missing or elapsed')
    seconds = (spec.last_trading_time-now).total_seconds()
    price = edge.executable_price_cents
    bucket = '1-2c' if price <= 2 else '3-5c' if price <= 5 else '6-10c' if price <= 10 else '>10c'
    lag = (observation_age <= 300 and probability_change is not None and book_change is not None
           and probability_change >= .05 and book_change < probability_change)
    data = dict(ticker=spec.ticker, event_key=event, captured_at=now.isoformat(), split=split,
                model_version='remaining-day-v1', side=side, price_cents=price,
                contracts=edge.fillable_contracts, fee_usd=edge.fee_usd,
                probability=edge.model_probability, net_ev_usd=edge.net_ev_usd,
                baseline_action=baseline, final_action='CONTROL' if control and final.startswith('BUY') else final,
                jev_action=jev, source=spec.official_source.value, station=spec.station_id or '',
                city=spec.city, market_type=spec.measurement.value, price_bucket=bucket,
                time_bucket='near_close' if seconds <= 7200 else 'mid_day' if seconds <= 21600 else 'early',
                seconds_to_close=seconds, observation_age=observation_age, surprise_f=surprise,
                forecast_disagreement_f=disagreement, spread_cents=edge.spread_cents,
                liquidity=edge.fillable_contracts, probability_change=probability_change,
                lag_candidate=int(lag), min_bid_cents=latest_bid, control=int(control))
    with db.transaction() as c:
        c.execute('INSERT OR IGNORE INTO research_candidates ('+','.join(data)+') VALUES ('+
                  ','.join('?' for _ in data)+')', tuple(data.values()))
        if latest_bid is not None:
            c.execute('UPDATE research_candidates SET min_bid_cents=MIN(COALESCE(min_bid_cents,?),?) '
                      'WHERE ticker=? AND side=?', (latest_bid, latest_bid, spec.ticker, side))
    return control


def report(db: Database, bankroll: float = 100) -> dict:
    account = paper_account(db, bankroll)
    with db.connect() as c:
        rows = c.execute("SELECT r.*,s.yes_outcome FROM research_candidates r JOIN settlements s "
                         "ON s.ticker=r.ticker AND s.final=1 WHERE r.id IN "
                         "(SELECT MIN(id) FROM research_candidates WHERE baseline_action LIKE 'BUY%' GROUP BY ticker)").fetchall()
    groups = defaultdict(lambda: {'markets':0, 'hypothetical_pnl':0., 'actual_pnl':0.,
                                  'winners_vetoed':0, 'losers_vetoed':0, 'jev_pnl_difference':0.})
    for r in rows:
        win = r['yes_outcome'] == (1 if r['side']=='yes' else 0)
        pnl = r['contracts']*(int(win)-r['price_cents']/100)-r['fee_usd']
        arms = ['A_baseline', 'C_'+r['time_bucket'], 'D_'+r['price_bucket'], 'E_'+r['source']]
        if r['lag_candidate']: arms.append('B_fresh_observation_lag')
        if r['control']: arms.append('F_no_trade_control')
        if r['jev_action'] is not None: arms.append('G_jev_reviewed')
        for arm in arms:
            g = groups[r['split']+'/'+arm]
            g['markets'] += 1
            g['hypothetical_pnl'] += pnl
            g['actual_pnl'] += account.order_results.get(r['order_id'], {}).get('pnl', 0)
            if r['jev_action'] is not None and r['final_action']=='SKIP':
                g['winners_vetoed' if win else 'losers_vetoed'] += 1
                g['jev_pnl_difference'] -= pnl
    return {'arms': dict(groups), 'note': 'Hypothetical outcomes are not fills. Holdout is evaluation only; freeze a new version before changing thresholds.'}
