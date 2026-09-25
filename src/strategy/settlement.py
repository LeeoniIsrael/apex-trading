"""Fail-closed parsing of individual Kalshi weather settlement rules."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from src.weather.stations import station_by_identifier


class MarketType(StrEnum):
    DAILY = "daily"
    HOURLY = "hourly"


class Measurement(StrEnum):
    HIGH = "high"
    LOW = "low"
    TEMPERATURE = "temperature"


class OfficialSource(StrEnum):
    NWS_CLI = "nws_cli"
    WEATHER_COMPANY = "weather_company"


@dataclass(frozen=True, slots=True)
class SettlementSpec:
    ticker: str
    event_ticker: str
    series_ticker: str
    city: str | None
    station_id: str | None
    nws_climate_product_id: str | None
    latitude: float | None
    longitude: float | None
    timezone: str | None
    standard_utc_offset_hours: int | None
    market_date: date | None
    market_type: MarketType | None
    measurement: Measurement | None
    threshold_low: float | None
    threshold_high: float | None
    inclusive_low: bool
    inclusive_high: bool
    official_source: OfficialSource | None
    preliminary_sources: tuple[str, ...]
    last_trading_time: datetime | None
    expected_settlement_time: datetime | None
    observation_window_start: datetime | None
    observation_window_end: datetime | None
    rounding_rules: str | None
    ambiguity_flags: tuple[str, ...] = field(default_factory=tuple)
    parse_confidence: float = 0.0
    raw_rules: str = ""

    @property
    def tradeable(self) -> bool:
        required = (
            self.ticker,
            self.station_id or self.nws_climate_product_id,
            self.timezone,
            self.market_date,
            self.market_type,
            self.measurement,
            self.official_source,
            self.observation_window_start,
            self.observation_window_end,
        )
        threshold_known = self.threshold_low is not None or self.threshold_high is not None
        return bool(all(required) and threshold_known and not self.ambiguity_flags
                    and self.parse_confidence >= 0.95)


_MONTHS = {m.upper(): i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _market_date(market: dict[str, Any], rules: str) -> date | None:
    ticker = str(market.get("event_ticker") or market.get("ticker") or "")
    match = re.search(r"-(\d{2})([A-Z]{3})(\d{2})(?:-|$)", ticker.upper())
    if match and match.group(2) in _MONTHS:
        return date(2000 + int(match.group(1)), _MONTHS[match.group(2)], int(match.group(3)))
    match = re.search(
        r"\b(?:for|on)\s+(?:[A-Za-z]+\s+)?([A-Z][a-z]{2,8})\s+(\d{1,2}),\s*(20\d{2})",
        rules,
    )
    if match:
        try:
            return datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
        except ValueError:
            try:
                return datetime.strptime(" ".join(match.groups()), "%b %d %Y").date()
            except ValueError:
                return None
    return None


def _thresholds(text: str, market: dict[str, Any]) -> tuple[float | None, float | None, bool, bool]:
    # Rule language determines inclusivity; structured strikes alone do not.
    patterns = (
        (r"(?:strictly\s+)?less than\s+(-?\d+(?:\.\d+)?)", "high", False),
        (r"(?:strictly\s+)?greater than\s+(-?\d+(?:\.\d+)?)", "low", False),
        (r"(?:at least|or above|above or equal to)\s+(-?\d+(?:\.\d+)?)", "low", True),
        (r"(?:at most|or below|below or equal to)\s+(-?\d+(?:\.\d+)?)", "high", True),
    )
    lowered = text.lower().replace("°", "")
    for pattern, boundary, inclusive in patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        value = float(match.group(1))
        if boundary == "low":
            return value, None, inclusive, False
        return None, value, False, inclusive
    match = re.search(r"(?:between\s+)?(-?\d+(?:\.\d+)?)\s*(?:to|through|and)\s*(-?\d+(?:\.\d+)?)", lowered)
    if match:
        return float(match.group(1)), float(match.group(2)), True, True
    # Strikes without explicit textual inclusivity are ambiguous.
    return None, None, False, False


def _fixed_standard_window(
    market_day: date,
    standard_offset_hours: int,
) -> tuple[datetime, datetime]:
    fixed = timezone(timedelta(hours=standard_offset_hours))
    start = datetime.combine(market_day, time.min, tzinfo=fixed).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def parse_settlement_spec(market: dict[str, Any]) -> SettlementSpec:
    """Parse a spec from one market payload; uncertainty becomes a hard flag."""
    rules = "\n".join(str(market.get(k) or "") for k in (
        "rules_primary", "rules_secondary", "title", "subtitle", "yes_sub_title",
    )).strip()
    lowered = rules.lower()
    flags: list[str] = []

    source: OfficialSource | None = None
    if "weather company" in lowered or "weather.com/kalshi" in lowered:
        source = OfficialSource.WEATHER_COMPANY
    if "climatological report" in lowered or "daily climate report" in lowered:
        if source is not None:
            flags.append("conflicting_official_sources")
        else:
            source = OfficialSource.NWS_CLI
    if source is None:
        flags.append("official_source_missing")

    hourly = bool(re.search(r"\b(hourly|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|[A-Z]{2,4}))\b", rules, re.I))
    market_type = MarketType.HOURLY if hourly else MarketType.DAILY
    if hourly:
        flags.append("hourly_execution_not_supported")
    if re.search(r"\b(highest|maximum|max temperature)\b", lowered):
        measurement = Measurement.HIGH
    elif re.search(r"\b(lowest|minimum|min temperature)\b", lowered):
        measurement = Measurement.LOW
    elif hourly:
        measurement = Measurement.TEMPERATURE
    else:
        measurement = None
        flags.append("measurement_missing")

    cli_match = re.search(r"\bCLI[A-Z]{3}\b", rules, re.I)
    station_match = re.search(r"\bK[A-Z]{3}\b", rules)
    cli_id = cli_match.group(0).upper() if cli_match else None
    station_id = station_match.group(0).upper() if station_match else None
    if station_id and cli_id and station_by_identifier(station_id) != station_by_identifier(cli_id):
        flags.append("conflicting_station_identifiers")
    station = station_by_identifier(station_id or cli_id)
    if station and station_id is None:
        station_id = station.station_id
    if station and cli_id is None:
        cli_id = station.climate_product_id
    if not station:
        flags.append("station_unresolved")

    market_day = _market_date(market, rules)
    if market_day is None:
        flags.append("market_date_missing")
    low, high, inclusive_low, inclusive_high = _thresholds(rules, market)
    if (any(v is not None and not math.isfinite(v) for v in (low, high))
        or (low is not None and high is not None and low > high)):
        flags.append("invalid_threshold")
    rule_day = _market_date({}, rules)
    if rule_day is not None and market_day != rule_day:
        flags.append("conflicting_market_dates")
    if low is None and high is None:
        flags.append("threshold_missing")

    window_start = window_end = None
    if station and market_day:
        if market_type == MarketType.DAILY and source == OfficialSource.NWS_CLI:
            window_start, window_end = _fixed_standard_window(
                market_day, station.standard_utc_offset_hours,
            )
        elif market_type == MarketType.DAILY:
            local = ZoneInfo(station.timezone)
            window_start = datetime.combine(market_day, time.min, local).astimezone(timezone.utc)
            window_end = (datetime.combine(market_day + timedelta(days=1), time.min, local)
                          .astimezone(timezone.utc))
        else:
            # Exact hourly time must be stated. Do not guess it from close time.
            tm = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", rules, re.I)
            if tm:
                hour = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "pm" else 0)
                minute = int(tm.group(2) or 0)
                local_dt = datetime.combine(market_day, time(hour, minute), ZoneInfo(station.timezone))
                window_start = local_dt.astimezone(timezone.utc)
                window_end = window_start + timedelta(minutes=1)
            else:
                flags.append("hourly_observation_time_missing")

    confidence = max(0.0, 1.0 - 0.15 * len(set(flags)))
    if not flags:
        confidence = 1.0
    rounding = ("final published integer Fahrenheit value; preliminary values may differ "
                "because of Celsius conversion and source revisions")
    return SettlementSpec(
        ticker=str(market.get("ticker") or ""),
        event_ticker=str(market.get("event_ticker") or ""),
        series_ticker=str(market.get("series_ticker") or ""),
        city=station.city if station else None,
        station_id=station_id,
        nws_climate_product_id=cli_id,
        latitude=station.latitude if station else None,
        longitude=station.longitude if station else None,
        timezone=station.timezone if station else None,
        standard_utc_offset_hours=station.standard_utc_offset_hours if station else None,
        market_date=market_day,
        market_type=market_type,
        measurement=measurement,
        threshold_low=low,
        threshold_high=high,
        inclusive_low=inclusive_low,
        inclusive_high=inclusive_high,
        official_source=source,
        preliminary_sources=("METAR", "NWS station observations") if source == OfficialSource.NWS_CLI else ("The Weather Company preliminary",),
        last_trading_time=_parse_iso(market.get("close_time")),
        expected_settlement_time=_parse_iso(market.get("expected_expiration_time")),
        observation_window_start=window_start,
        observation_window_end=window_end,
        rounding_rules=rounding,
        ambiguity_flags=tuple(sorted(set(flags))),
        parse_confidence=confidence,
        raw_rules=rules,
    )


def value_in_contract(value: float, spec: SettlementSpec) -> bool:
    if spec.threshold_low is not None:
        if value < spec.threshold_low or (value == spec.threshold_low and not spec.inclusive_low):
            return False
    if spec.threshold_high is not None:
        if value > spec.threshold_high or (value == spec.threshold_high and not spec.inclusive_high):
            return False
    return True
