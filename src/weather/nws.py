"""NWS station observations and hourly forecast provider."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.network import ResilientJSONSession
from src.weather.forecasts import ForecastRun, HourlyForecast
from src.weather.observations import RawObservation, ReportType
from src.weather.stations import WeatherStation


class NWSProvider:
    base_url = "https://api.weather.gov"

    def __init__(self, user_agent: str, timeout_seconds: float = 10.0) -> None:
        if not user_agent.strip():
            raise ValueError("NWS requires an identifying User-Agent")
        self.headers = {"User-Agent": user_agent, "Accept": "application/geo+json"}
        self.timeout_seconds = timeout_seconds
        self.http = ResilientJSONSession(
            timeout_seconds=timeout_seconds, headers=self.headers,
        )

    def _get(self, url: str) -> dict[str, Any]:
        return self.http.get(url)

    def fetch_station_observations(self, station: WeatherStation, limit: int = 100) -> list[RawObservation]:
        payload = self._get(f"{self.base_url}/stations/{station.station_id}/observations?limit={limit}")
        ingested = datetime.now(timezone.utc)
        result: list[RawObservation] = []
        for feature in payload.get("features", []):
            props = feature.get("properties", {})
            timestamp = props.get("timestamp")
            if not timestamp:
                continue
            result.append(RawObservation(
                station_id=station.station_id,
                source="api.weather.gov",
                report_type=ReportType.NWS_OBSERVATION,
                observed_at_utc=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                ingested_at_utc=ingested,
                payload=str(props.get("rawMessage") or props),
            ))
        return result

    def fetch_hourly_forecast(self, station: WeatherStation) -> ForecastRun:
        point = self._get(f"{self.base_url}/points/{station.latitude},{station.longitude}")
        forecast_url = point["properties"]["forecastHourly"]
        payload = self._get(forecast_url)
        generated = datetime.fromisoformat(
            payload["properties"]["generatedAt"].replace("Z", "+00:00")
        )
        hours: list[HourlyForecast] = []
        for period in payload["properties"].get("periods", []):
            temperature = float(period["temperature"])
            if period.get("temperatureUnit") == "C":
                temperature = temperature * 9 / 5 + 32
            humidity = (period.get("relativeHumidity") or {}).get("value")
            precip = (period.get("probabilityOfPrecipitation") or {}).get("value")
            hours.append(HourlyForecast(
                valid_at_utc=datetime.fromisoformat(period["startTime"]).astimezone(timezone.utc),
                temperature_f=temperature,
                relative_humidity_pct=float(humidity) if humidity is not None else None,
                precipitation_probability=(float(precip) / 100 if precip is not None else None),
            ))
        return ForecastRun(
            provider="NWS", station_id=station.station_id, model="NDFD hourly",
            generated_at_utc=generated, ingested_at_utc=datetime.now(timezone.utc),
            members=(tuple(hours),),
        )
