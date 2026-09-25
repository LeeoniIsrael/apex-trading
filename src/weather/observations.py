"""Raw/normalized observation models and conservative METAR decoding."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from statistics import median
from zoneinfo import ZoneInfo


class ObservationStatus(StrEnum):
    PRELIMINARY = "preliminary"
    FINAL = "final"


class ReportType(StrEnum):
    METAR = "metar"
    SPECI = "speci"
    NWS_OBSERVATION = "nws_observation"
    SIX_HOUR_EXTREME = "six_hour_extreme"
    TWENTY_FOUR_HOUR_EXTREME = "twenty_four_hour_extreme"
    DAILY_CLIMATE = "daily_climate"
    WEATHER_COMPANY = "weather_company"


@dataclass(frozen=True, slots=True)
class RawObservation:
    station_id: str
    source: str
    report_type: ReportType
    observed_at_utc: datetime
    ingested_at_utc: datetime
    payload: str
    status: ObservationStatus = ObservationStatus.PRELIMINARY


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    station_id: str
    source: str
    report_type: ReportType
    observed_at_utc: datetime
    observed_at_local: datetime
    ingested_at_utc: datetime
    temperature_raw: str | None
    raw_payload: str
    temperature_f: float | None
    max_temperature_f: float | None
    min_temperature_f: float | None
    status: ObservationStatus
    # Only an explicitly established interval can contribute aggregate extrema.
    extreme_window_start: datetime | None = None
    extreme_window_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class ObservationCadence:
    station_id: str
    routine_minutes: tuple[int, ...]
    median_interval_seconds: float | None
    special_observation_count: int
    sample_size: int

    def seconds_to_poll_window(self, now: datetime, window_minutes: int = 5) -> int | None:
        if not self.routine_minutes:
            return None
        minute = now.minute
        distances = [min((m - minute) % 60, (minute - m) % 60) for m in self.routine_minutes]
        return min(distances) * 60 if min(distances) <= window_minutes else None


def _signed_tenths(value: str) -> float:
    sign = -1 if value[0] == "1" else 1
    return sign * int(value[1:]) / 10.0


def celsius_to_fahrenheit(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def parse_metar(raw: RawObservation, local_timezone: str) -> NormalizedObservation:
    """Decode current temperature and 6h/24h extrema when encoded in remarks.

    The `TsnTTTsnTdTdTd` group is tenths Celsius.  `1snTxTxTx`/`2snTnTnTn`
    are six-hour extrema and `4snTxTxTxsnTnTnTn` is the 24-hour group.
    Values remain preliminary and never become settlement truth here.
    """
    text = raw.payload.upper()
    temp_c: float | None = None
    max_c: float | None = None
    min_c: float | None = None

    precise = re.search(r"(?:^|\s)T([01]\d{3})([01]\d{3})(?:\s|$)", text)
    if precise:
        temp_c = _signed_tenths(precise.group(1))
    else:
        whole = re.search(r"(?:^|\s)(M?\d{2})/(M?\d{2})(?:\s|$)", text)
        if whole:
            token = whole.group(1)
            temp_c = float(-int(token[1:]) if token.startswith("M") else int(token))

    daily = re.search(r"(?:^|\s)4([01]\d{3})([01]\d{3})(?:\s|$)", text)
    if daily:
        max_c, min_c = _signed_tenths(daily.group(1)), _signed_tenths(daily.group(2))
    else:
        six_max = re.search(r"(?:^|\s)1([01]\d{3})(?:\s|$)", text)
        six_min = re.search(r"(?:^|\s)2([01]\d{3})(?:\s|$)", text)
        if six_max:
            max_c = _signed_tenths(six_max.group(1))
        if six_min:
            min_c = _signed_tenths(six_min.group(1))

    def f(value: float | None) -> float | None:
        return None if value is None else round(celsius_to_fahrenheit(value), 2)

    return NormalizedObservation(
        station_id=raw.station_id,
        source=raw.source,
        report_type=raw.report_type,
        observed_at_utc=raw.observed_at_utc.astimezone(timezone.utc),
        observed_at_local=raw.observed_at_utc.astimezone(ZoneInfo(local_timezone)),
        ingested_at_utc=raw.ingested_at_utc.astimezone(timezone.utc),
        temperature_raw=precise.group(1) if precise else None,
        raw_payload=raw.payload,
        temperature_f=f(temp_c),
        max_temperature_f=f(max_c),
        min_temperature_f=f(min_c),
        status=raw.status,
    )


def infer_observation_cadence(
    station_id: str,
    observations: list[RawObservation],
) -> ObservationCadence:
    ordered = sorted(observations, key=lambda item: item.observed_at_utc)
    routine = [item for item in ordered if item.report_type == ReportType.METAR]
    minute_counts = Counter(item.observed_at_utc.minute for item in routine)
    # Retain minutes representing at least 20% of routine reports. This permits
    # real cadence shifts without pretending every incidental minute is routine.
    floor = max(2, math.ceil(len(routine) * 0.2)) if routine else 2
    minutes = tuple(sorted(minute for minute, count in minute_counts.items() if count >= floor))
    intervals = [
        (right.observed_at_utc - left.observed_at_utc).total_seconds()
        for left, right in zip(routine, routine[1:])
        if right.observed_at_utc > left.observed_at_utc
    ]
    return ObservationCadence(
        station_id=station_id,
        routine_minutes=minutes,
        median_interval_seconds=median(intervals) if intervals else None,
        special_observation_count=sum(item.report_type == ReportType.SPECI for item in ordered),
        sample_size=len(ordered),
    )
