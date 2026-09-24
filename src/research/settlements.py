"""Official settlement ingestion and paper-position reconciliation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.storage.database import Database
from src.strategy.settlement import OfficialSource, parse_settlement_spec, value_in_contract
from src.weather.climate_reports import NWSClimateProvider
from src.weather.stations import station_by_identifier
from src.weather.weather_company import WeatherCompanyProvider


class SettlementReconciler:
    def __init__(self, database: Database, *, nws_user_agent: str,
                 twc: WeatherCompanyProvider | None = None,
                 nws: NWSClimateProvider | None = None) -> None:
        self.database = database
        self.twc = twc or WeatherCompanyProvider()
        self.nws = nws or NWSClimateProvider(nws_user_agent)

    def reconcile(self) -> int:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT m.ticker,m.raw_json FROM markets m "
                "JOIN model_predictions p ON p.ticker=m.ticker "
                "WHERE p.eventual_outcome IS NULL"
            ).fetchall()
        settled = 0
        result_cache: dict[tuple[str, str, str], tuple[float | None, str] | None] = {}
        for row in rows:
            market: dict[str, Any] = json.loads(row["raw_json"])
            spec = parse_settlement_spec(market)
            if not spec.tradeable or spec.market_date is None or spec.official_source is None:
                continue
            station = station_by_identifier(spec.station_id or spec.nws_climate_product_id)
            if station is None:
                continue
            official_value: float | None = None
            raw_payload = ""
            source = spec.official_source.value
            cache_key = (source, station.station_id, spec.market_date.isoformat())
            if cache_key not in result_cache:
                if spec.official_source == OfficialSource.WEATHER_COMPANY:
                    result = self.twc.daily_result(station, spec.market_date)
                    if not result or not result.official:
                        result_cache[cache_key] = None
                    else:
                        # Cache both extrema as JSON so high and low contracts share one request.
                        result_cache[cache_key] = (
                            result.maximum_f,
                            json.dumps({"raw": result.raw, "minimum_f": result.minimum_f}, sort_keys=True),
                        )
                else:
                    report = self.nws.latest(station, spec.market_date)
                    if report is None:
                        result_cache[cache_key] = None
                    else:
                        result_cache[cache_key] = (
                            report.maximum_f,
                            json.dumps({"raw": report.raw_text, "minimum_f": report.minimum_f}),
                        )
            cached = result_cache[cache_key]
            if cached is None:
                continue
            maximum, cache_payload = cached
            cache_data = json.loads(cache_payload)
            official_value = (cache_data.get("minimum_f")
                              if spec.measurement and spec.measurement.value == "low"
                              else maximum)
            raw_payload = (json.dumps(cache_data.get("raw"), sort_keys=True)
                           if isinstance(cache_data.get("raw"), dict)
                           else str(cache_data.get("raw") or ""))
            if official_value is None:
                continue
            yes_outcome = int(value_in_contract(official_value, spec))
            now = datetime.now(timezone.utc).isoformat()
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO settlements(ticker,official_value,yes_outcome,source,final," 
                    "settled_at,raw_payload) VALUES(?,?,?,?,1,?,?) ON CONFLICT(ticker) DO NOTHING",
                    (spec.ticker, official_value, yes_outcome, source, now, raw_payload),
                )
                positions = connection.execute(
                    "SELECT side,contracts,average_price_cents FROM positions "
                    "WHERE ticker=? AND contracts>0", (spec.ticker,)
                ).fetchall()
                for position in positions:
                    wins = yes_outcome == 1 if position["side"] == "yes" else yes_outcome == 0
                    payout = position["contracts"] if wins else 0.0
                    cost = position["contracts"] * position["average_price_cents"] / 100
                    fees = connection.execute(
                        "SELECT COALESCE(SUM(fee_usd),0) FROM fills WHERE ticker=? AND side=?",
                        (spec.ticker, position["side"]),
                    ).fetchone()[0]
                    pnl = payout - cost - float(fees)
                    connection.execute(
                        "UPDATE positions SET contracts=0,realized_pnl_usd=realized_pnl_usd+?," 
                        "updated_at=? WHERE ticker=? AND side=?",
                        (pnl, now, spec.ticker, position["side"]),
                    )
                connection.execute(
                    "UPDATE model_predictions SET eventual_outcome=? WHERE ticker=?",
                    (yes_outcome, spec.ticker),
                )
            settled += 1
        return settled
