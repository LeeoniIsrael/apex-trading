"""Causal weather-error sample: one final Weather Company outcome per station-day.

Only forecasts saved before a day starts can estimate error. Holdout days are
reported separately and never tune live uncertainty.
"""
from datetime import datetime, timezone
from math import isfinite
from statistics import median
import json

from src.research.experiments import allocation
from src.strategy.settlement import OfficialSource, parse_settlement_spec


def error_summary(database, now=None):
    now = now or datetime.now(timezone.utc)
    development, holdout, seen = [], [], set()
    with database.connect() as c:
        rows = c.execute("SELECT s.ticker,s.official_value,s.settled_at,m.raw_json "
                         "FROM settlements s JOIN markets m ON m.ticker=s.ticker "
                         "WHERE s.final=1 AND s.source='weather_company' AND s.official_value IS NOT NULL").fetchall()
        for row in rows:
            spec = parse_settlement_spec(json.loads(row['raw_json']))
            if (not spec.tradeable or spec.official_source != OfficialSource.WEATHER_COMPANY
                    or not spec.observation_window_start or not spec.observation_window_end
                    or datetime.fromisoformat(row['settled_at']) >= now):
                continue
            key = (spec.station_id, spec.market_date, spec.measurement)
            if key in seen:
                continue
            seen.add(key)
            run = c.execute("SELECT id FROM forecast_runs WHERE station_id=? "
                            "AND provider='Open-Meteo' AND generated_at_utc<=? AND ingested_at_utc<=? "
                            "ORDER BY generated_at_utc DESC LIMIT 1",
                            (spec.station_id,spec.observation_window_start.isoformat(),
                             spec.observation_window_start.isoformat())).fetchone()
            if not run:
                continue
            points = c.execute("SELECT member,temperature_f FROM weather_forecasts "
                               "WHERE forecast_run_id=? AND valid_at_utc>=? AND valid_at_utc<?",
                               (run['id'],spec.observation_window_start.isoformat(),
                                spec.observation_window_end.isoformat())).fetchall()
            members = {}
            for p in points:
                if isfinite(p['temperature_f']):
                    members.setdefault(p['member'],[]).append(p['temperature_f'])
            extremes = [(min(v) if spec.measurement.value=='low' else max(v))
                        for v in members.values() if len(v)>=12]
            if not extremes or not isfinite(row['official_value']):
                continue
            error = float(row['official_value'])-median(extremes)
            split,_ = allocation('|'.join((spec.station_id,str(spec.market_date))))
            (holdout if split=='holdout' else development).append(error)
    # Do not shrink below a cautious fallback merely because the sample is tiny.
    spread = max(4.0,1.4826*median(abs(x) for x in development)) if len(development)>=30 else 4.0
    return {'training_days':len(development),'holdout_days':len(holdout),
            'forecast_error_std_f':round(spread,3),
            'holdout_mae_f':round(sum(abs(x) for x in holdout)/len(holdout),3) if holdout else None}
