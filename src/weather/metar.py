"""Aviation Weather Center METAR ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.network import ResilientJSONSession
from src.weather.observations import RawObservation, ReportType


class MetarProvider:
    url = "https://aviationweather.gov/api/data/metar"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.http = ResilientJSONSession(
            timeout_seconds=timeout_seconds,
            headers={"User-Agent": "APEX-Weather/2.0 contact=operator"},
        )

    def fetch(self, station_id: str, hours: int = 6) -> list[RawObservation]:
        payload = self.http.get(
            self.url,
            params={"ids": station_id, "format": "json", "hours": hours},
        )
        return self.parse(station_id, payload)

    @staticmethod
    def parse(station_id: str, payload: list[dict[str, Any]]) -> list[RawObservation]:
        ingested = datetime.now(timezone.utc)
        observations: list[RawObservation] = []
        for item in payload:
            raw_text = str(item.get("rawOb") or item.get("raw_text") or "")
            observed = item.get("obsTime") or item.get("reportTime")
            if not raw_text or not observed:
                continue
            if isinstance(observed, (int, float)):
                observed_at = datetime.fromtimestamp(observed, timezone.utc)
            else:
                observed_at = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
            report_type = ReportType.SPECI if raw_text.lstrip().startswith("SPECI") else ReportType.METAR
            observations.append(RawObservation(
                station_id=station_id.upper(), source="aviationweather.gov",
                report_type=report_type, observed_at_utc=observed_at,
                ingested_at_utc=ingested, payload=raw_text,
            ))
        return observations
