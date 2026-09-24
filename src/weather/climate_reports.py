"""NWS Daily Climate Report retrieval and conservative value parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.network import ResilientJSONSession
from src.weather.stations import WeatherStation


@dataclass(frozen=True, slots=True)
class ClimateReport:
    product_id: str
    issued_at: datetime
    report_date: date
    maximum_f: float | None
    minimum_f: float | None
    raw_text: str


def parse_cli_product(product_id: str, issued_at: datetime, text: str) -> ClimateReport:
    date_match = re.search(
        r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(20\d{2})", text, re.I,
    )
    if not date_match:
        raise ValueError("CLI report date not found")
    report_date = datetime.strptime(" ".join(date_match.groups()), "%B %d %Y").date()
    max_match = re.search(r"^\s*MAXIMUM\s+(-?\d+(?:\.\d+)?)\b", text, re.I | re.M)
    min_match = re.search(r"^\s*MINIMUM\s+(-?\d+(?:\.\d+)?)\b", text, re.I | re.M)
    return ClimateReport(
        product_id=product_id, issued_at=issued_at, report_date=report_date,
        maximum_f=float(max_match.group(1)) if max_match else None,
        minimum_f=float(min_match.group(1)) if min_match else None,
        raw_text=text,
    )


class NWSClimateProvider:
    base_url = "https://api.weather.gov"

    def __init__(self, user_agent: str, timeout_seconds: float = 10.0) -> None:
        self.headers = {"User-Agent": user_agent, "Accept": "application/ld+json"}
        self.timeout_seconds = timeout_seconds
        self.http = ResilientJSONSession(
            timeout_seconds=timeout_seconds, headers=self.headers,
        )

    def latest(self, station: WeatherStation, report_date: date) -> ClimateReport | None:
        listing = self.http.get(
            f"{self.base_url}/products/types/CLI/locations/{station.nws_office}",
        )
        candidates = listing.get("@graph", [])
        for item in candidates:
            product_url = item.get("@id")
            if not product_url:
                continue
            payload: dict[str, Any] = self.http.get(product_url)
            text = str(payload.get("productText") or "")
            if station.climate_product_id not in text.upper():
                continue
            issued = datetime.fromisoformat(str(payload["issuanceTime"]).replace("Z", "+00:00"))
            report = parse_cli_product(str(payload.get("id") or product_url), issued, text)
            if report.report_date == report_date:
                return report
        return None
