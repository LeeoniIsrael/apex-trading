"""Official Weather Company settlement feed exposed at weather.com/kalshi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from typing import Any
from zoneinfo import ZoneInfo

from src.network import ResilientJSONSession
from src.weather.observations import (
    NormalizedObservation,
    ObservationStatus,
    ReportType,
)
from src.weather.stations import WeatherStation


@dataclass(frozen=True, slots=True)
class WeatherCompanyDailyResult:
    station_id: str
    report_date: date
    maximum_f: float | None
    minimum_f: float | None
    status: str
    official: bool
    raw: dict[str, Any]


class WeatherCompanyProvider:
    """Reads the exact public site named in current Kalshi weather rules."""

    base_url = "https://weather.com/kalshi/api"

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.headers = {"User-Agent": "APEX-Weather/2.0"}
        self.http = ResilientJSONSession(
            timeout_seconds=timeout_seconds, headers=self.headers,
        )

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.http.get(f"{self.base_url}{path}", params=params)

    def hourly(self, station: WeatherStation) -> list[NormalizedObservation]:
        payload = self._get("/metar", {"station": station.station_id})
        ingested = datetime.now(timezone.utc)
        observations: list[NormalizedObservation] = []
        for station_payload in payload.get("stations", []):
            if str(station_payload.get("icaoId", "")).upper() != station.station_id:
                continue
            for item in station_payload.get("observations", []):
                timestamp = item.get("reportTimeUTC")
                if not timestamp or item.get("tempF") is None:
                    continue
                observed_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                observations.append(NormalizedObservation(
                    station_id=station.station_id,
                    source="weather.com/kalshi",
                    report_type=ReportType.WEATHER_COMPANY,
                    observed_at_utc=observed_at.astimezone(timezone.utc),
                    observed_at_local=observed_at.astimezone(ZoneInfo(station.timezone)),
                    ingested_at_utc=ingested,
                    temperature_raw=str(item.get("tempC")),
                    raw_payload=json.dumps(item, sort_keys=True),
                    temperature_f=float(item["tempF"]),
                    max_temperature_f=None,
                    min_temperature_f=None,
                    status=(ObservationStatus.FINAL if item.get("status") == "settled"
                            else ObservationStatus.PRELIMINARY),
                ))
        return observations

    def daily_result(self, station: WeatherStation, report_date: date) -> WeatherCompanyDailyResult | None:
        payload = self._get("/climate/primary", {"date": report_date.isoformat()})
        for item in payload.get("results", []):
            station_payload = item.get("station", {})
            if str(station_payload.get("icao", "")).upper() != station.station_id:
                continue
            data = item.get("data") or {}
            status = str(item.get("status") or "no_report")
            return WeatherCompanyDailyResult(
                station_id=station.station_id,
                report_date=report_date,
                maximum_f=float(data["maxTemp"]) if data.get("maxTemp") is not None else None,
                minimum_f=float(data["minTemp"]) if data.get("minTemp") is not None else None,
                status=status,
                official=bool(data.get("isOfficial")) and status in {"official", "revised"},
                raw=item,
            )
        return None
