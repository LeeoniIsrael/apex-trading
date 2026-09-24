"""Provider-neutral forecast records and staleness checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True, slots=True)
class HourlyForecast:
    valid_at_utc: datetime
    temperature_f: float
    cloud_cover_pct: float | None = None
    wind_speed_mph: float | None = None
    relative_humidity_pct: float | None = None
    dewpoint_f: float | None = None
    precipitation_probability: float | None = None


@dataclass(frozen=True, slots=True)
class ForecastRun:
    provider: str
    station_id: str
    model: str
    generated_at_utc: datetime
    ingested_at_utc: datetime
    members: tuple[tuple[HourlyForecast, ...], ...]

    def is_stale(self, now: datetime, max_age: timedelta) -> bool:
        return now.astimezone(timezone.utc) - self.generated_at_utc.astimezone(timezone.utc) > max_age

    @property
    def member_count(self) -> int:
        return len(self.members)

