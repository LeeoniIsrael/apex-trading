"""Public market discovery and settlement-spec research collection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from src.kalshi.client import KalshiClientV2
from src.storage.database import Database
from src.strategy.settlement import SettlementSpec, parse_settlement_spec


_WEATHER_MARKERS = (
    "temperature", "highest temperature", "lowest temperature", "weather",
    "rain", "snow", "KXHIGH", "KXLOW", "KXTEMP",
)

_TEMPERATURE_SERIES_PATTERN = (
    "highest temperature", "lowest temperature", "maximum temperature",
    "hourly directional", "low temperature",
)


def _weather_candidate(market: dict[str, Any]) -> bool:
    haystack = " ".join(str(market.get(key) or "") for key in (
        "ticker", "series_ticker", "title", "subtitle", "category",
        "rules_primary", "rules_secondary",
    )).lower()
    return any(marker.lower() in haystack for marker in _WEATHER_MARKERS)


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _price_cents(value: Any) -> int | None:
    try:
        cents = round(float(value) * 100)
        return cents if 0 <= cents <= 100 else None
    except (TypeError, ValueError):
        return None


class MarketCollector:
    def __init__(self, client: KalshiClientV2, database: Database) -> None:
        self.client = client
        self.database = database

    def discover(self, page_limit: int = 1000, max_pages: int = 10) -> list[SettlementSpec]:
        candidates: list[dict[str, Any]] = []
        series_payload = self.client.get_series_list(category="Climate and Weather")
        temperature_series = [
            series for series in series_payload.get("series", [])
            if any(marker in str(series.get("title") or "").lower()
                   for marker in _TEMPERATURE_SERIES_PATTERN)
        ]
        # max_pages is retained as an operator cap over series batches.
        for series in temperature_series[: max_pages * 50]:
            series_ticker = str(series.get("ticker") or "")
            if not series_ticker:
                continue
            payload = self.client.get_markets(
                status="open", series_ticker=series_ticker, limit=page_limit,
            )
            for market in payload.get("markets", []):
                if _weather_candidate(market):
                    market["series_ticker"] = series_ticker
                    market["_fee_type"] = series.get("fee_type")
                    market["_fee_multiplier"] = series.get("fee_multiplier")
                    market["_settlement_sources"] = series.get("settlement_sources")
                    candidates.append(market)

        now = datetime.now(timezone.utc).isoformat()
        specs: list[SettlementSpec] = []
        with self.database.transaction() as connection:
            for summary in candidates:
                ticker = str(summary.get("ticker") or "")
                if not ticker:
                    continue
                market = summary
                spec = parse_settlement_spec(market)
                specs.append(spec)
                raw_json = json.dumps(market, sort_keys=True, default=_json_default)
                connection.execute(
                    "INSERT INTO markets(ticker,event_ticker,series_ticker,status,raw_json,observed_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
                    "event_ticker=excluded.event_ticker,series_ticker=excluded.series_ticker," 
                    "status=excluded.status,raw_json=excluded.raw_json,observed_at=excluded.observed_at",
                    (ticker, market.get("event_ticker"), market.get("series_ticker"),
                     market.get("status", "open"), raw_json, now),
                )
                connection.execute(
                    "INSERT INTO market_snapshots(ticker,captured_at,yes_bid_cents,yes_ask_cents,"
                    "no_bid_cents,no_ask_cents,volume,raw_json) VALUES(?,?,?,?,?,?,?,?)",
                    (ticker, now, _price_cents(market.get("yes_bid_dollars")),
                     _price_cents(market.get("yes_ask_dollars")),
                     _price_cents(market.get("no_bid_dollars")),
                     _price_cents(market.get("no_ask_dollars")),
                     float(market.get("volume_fp") or 0), raw_json),
                )
                rules_hash = hashlib.sha256(spec.raw_rules.encode()).hexdigest()
                connection.execute(
                    "INSERT OR IGNORE INTO market_rules(ticker,rules_text,rules_hash,retrieved_at) "
                    "VALUES(?,?,?,?)", (ticker, spec.raw_rules, rules_hash, now),
                )
                spec_json = json.dumps(asdict(spec), sort_keys=True, default=_json_default)
                connection.execute(
                    "INSERT INTO settlement_specs(ticker,spec_json,parse_confidence,tradeable," 
                    "ambiguity_flags,updated_at) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(ticker) DO UPDATE SET spec_json=excluded.spec_json," 
                    "parse_confidence=excluded.parse_confidence,tradeable=excluded.tradeable," 
                    "ambiguity_flags=excluded.ambiguity_flags,updated_at=excluded.updated_at",
                    (ticker, spec_json, spec.parse_confidence, int(spec.tradeable),
                     json.dumps(spec.ambiguity_flags), now),
                )
        return specs
