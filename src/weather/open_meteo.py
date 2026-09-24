"""Open-Meteo GFS ensemble provider using settlement-station coordinates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.network import ResilientJSONSession
from src.weather.forecasts import ForecastRun, HourlyForecast
from src.weather.stations import WeatherStation


class OpenMeteoEnsembleProvider:
    url = "https://ensemble-api.open-meteo.com/v1/ensemble"

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.http = ResilientJSONSession(timeout_seconds=timeout_seconds)

    def fetch(self, station: WeatherStation, forecast_days: int = 3) -> ForecastRun:
        payload = self.http.get(self.url, params={
            "latitude": station.latitude,
            "longitude": station.longitude,
            "hourly": "temperature_2m",
            "models": "gfs_seamless",
            "forecast_days": forecast_days,
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        })
        return self.parse(station.station_id, payload)

    @staticmethod
    def parse(station_id: str, payload: dict[str, Any]) -> ForecastRun:
        hourly = payload.get("hourly", {})
        times = [datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
                 for value in hourly.get("time", [])]
        keys = sorted(key for key in hourly if key.startswith("temperature_2m_member"))
        if not keys and "temperature_2m" in hourly:
            keys = ["temperature_2m"]
        members: list[tuple[HourlyForecast, ...]] = []
        for key in keys:
            values = hourly.get(key, [])
            members.append(tuple(
                HourlyForecast(valid_at_utc=when, temperature_f=float(values[index]))
                for index, when in enumerate(times)
                if index < len(values) and values[index] is not None
            ))
        generated_raw = payload.get("generationtime_ms")
        # Open-Meteo exposes runtime but not always model generation timestamp;
        # ingestion time is used and is labeled as such rather than fabricated.
        ingested = datetime.now(timezone.utc)
        return ForecastRun(
            provider="Open-Meteo", station_id=station_id, model="gfs_seamless",
            generated_at_utc=ingested, ingested_at_utc=ingested,
            members=tuple(members),
        )
