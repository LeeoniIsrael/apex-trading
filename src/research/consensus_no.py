"""Frozen NO-value shadow strategy; has no execution or account-control path."""
import json
import math
from datetime import datetime

from src.research.experiments import MODEL_VERSION, allocation
from src.strategy.settlement import MarketType, OfficialSource, parse_settlement_spec

VERSION = 'fresh-consensus-no-v1'
START = '2026-10-21T00:00:00+00:00'


def qualifies(row):
    """Use a fixed 90% probability ceiling, not a claim of calibrated accuracy."""
    fields = ('probability', 'price_cents', 'contracts', 'fee_usd',
              'spread_cents', 'observation_age', 'forecast_disagreement_f',
              'seconds_to_close')
    if any(row[k] is None or not math.isfinite(row[k]) for k in fields):
        return False
    q = row['contracts']
    return (row['side'] == 'no' and row['source'] == 'weather_company'
            and .90 <= row['probability'] <= 1
            and 1 <= row['price_cents'] <= 85 and q > 0 and q == int(q)
            and 0 <= row['fee_usd'] and q*row['price_cents']/100+row['fee_usd'] <= 2.000001
            and 0 <= row['spread_cents'] <= 8
            and 0 <= row['observation_age'] <= 1800
            and 0 <= row['forecast_disagreement_f'] <= 3
            and 0 < row['seconds_to_close'] <= 21600
            and .90-row['price_cents']/100-row['fee_usd']/q >= .05-1e-9)


def source_valid(spec, now):
    return (spec.tradeable and spec.market_type == MarketType.DAILY
            and spec.official_source == OfficialSource.WEATHER_COMPANY
            and spec.observation_window_start <= now < spec.observation_window_end
            and spec.last_trading_time is not None and now < spec.last_trading_time)


def record_signal(database, *, spec, now, edge, observation_age, disagreement,
                  forecasts_fresh, evidence):
    if now < datetime.fromisoformat(START) or not forecasts_fresh or not source_valid(spec, now):
        return False
    row = dict(side=edge.side, source=spec.official_source.value,
               probability=edge.model_probability, price_cents=edge.executable_price_cents,
               contracts=edge.contracts, fee_usd=edge.fee_usd,
               spread_cents=edge.spread_cents, observation_age=observation_age,
               forecast_disagreement_f=disagreement,
               seconds_to_close=(spec.last_trading_time-now).total_seconds())
    if not qualifies(row):
        return False
    event = '|'.join((spec.station_id, str(spec.market_date)))
    split, _ = allocation(event)
    with database.transaction() as c:
        cursor = c.execute(
            'INSERT OR IGNORE INTO shadow_signals(strategy_version,event_key,ticker,'
            'split,captured_at,side,price_cents,contracts,fee_usd,probability,evidence_json) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (VERSION,event,spec.ticker,split,now.isoformat(),'no',row['price_cents'],
             row['contracts'],row['fee_usd'],.90,
             json.dumps({'primary_rule':spec.raw_rules,'inputs':row,**evidence},default=str)),
        )
        return cursor.rowcount == 1


def replay(database, split):
    """Diagnostic replay of saved best-side candidates, using their saved asks.

    Selection precedes the outcome join. No sibling is substituted if the first
    selected market lacks a final outcome. Previously inspected holdout data is
    diagnostic, not a fresh validation set for this new strategy.
    """
    if split not in ('development', 'holdout'):
        raise ValueError('unknown split')
    with database.connect() as c:
        rows = c.execute(
            "SELECT r.*,m.raw_json FROM research_candidates r JOIN markets m ON m.ticker=r.ticker "
            "WHERE r.model_version=? AND r.split=? AND r.side='no' AND r.probability>=.90 "
            "AND r.price_cents<=85 AND r.spread_cents<=8 AND r.seconds_to_close<=21600 "
            "ORDER BY r.captured_at,r.id", (MODEL_VERSION,split)).fetchall()
        selected = {}
        for row in rows:
            if row['event_key'] in selected or not qualifies(row):
                continue
            spec = parse_settlement_spec(json.loads(row['raw_json']))
            now = datetime.fromisoformat(row['captured_at'])
            fresh = True
            for provider, max_age in (('Open-Meteo',28800),('NWS',10800)):
                run = c.execute(
                    'SELECT generated_at_utc FROM forecast_runs WHERE station_id=? '
                    'AND provider=? AND generated_at_utc<=? AND ingested_at_utc<=? '
                    'ORDER BY generated_at_utc DESC LIMIT 1',
                    (spec.station_id,provider,row['captured_at'],row['captured_at'])).fetchone()
                if not run or not 0 <= (now-datetime.fromisoformat(run[0])).total_seconds() <= max_age:
                    fresh = False
            if source_valid(spec,now) and fresh:
                selected[row['event_key']] = row
        returns = []
        wins = 0
        for row in selected.values():
            outcome = c.execute('SELECT yes_outcome,settled_at FROM settlements '
                                'WHERE ticker=? AND final=1',(row['ticker'],)).fetchone()
            if not outcome or datetime.fromisoformat(outcome['settled_at']) <= datetime.fromisoformat(row['captured_at']):
                continue
            won = 1-outcome['yes_outcome']
            wins += won
            returns.append(row['contracts']*(won-row['price_cents']/100)-row['fee_usd'])
    return {'split':split,'selected_events':len(selected),'settled_events':len(returns),
            'wins':wins,'hypothetical_after_fee_pnl_usd':round(sum(returns),2),
            'note':'Saved-ask replay; fills and profit are hypothetical. Prospective evidence starts at '+START}
