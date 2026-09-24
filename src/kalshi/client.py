"""Timeout/retry-aware Kalshi REST client with idempotent order creation."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

from src.kalshi.auth import KalshiSigner

logger = logging.getLogger(__name__)

PRODUCTION_URL = "https://external-api.kalshi.com/trade-api/v2"
DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"


class KalshiAPIError(RuntimeError):
    pass


@dataclass(slots=True)
class KalshiClientV2:
    base_url: str = PRODUCTION_URL
    signer: KalshiSigner | None = None
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    _base_path: str = field(init=False, repr=False)
    session: requests.Session = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._base_path = urlparse(self.base_url).path.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        path = f"{self._base_path}{endpoint}"
        if authenticated and self.signer is None:
            raise KalshiAPIError("authenticated request attempted without credentials")
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            headers = self.signer.headers(method, path) if authenticated and self.signer else {}
            try:
                response = self.session.request(
                    method, f"{self.base_url}{endpoint}", params=params, json=json_body,
                    headers=headers, timeout=self.timeout_seconds,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < self.max_attempts:
                    time.sleep(min(0.5 * 2**attempt, 2.0))
                    continue
                response.raise_for_status()
                return response.json()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts and not (
                    isinstance(exc, requests.HTTPError)
                    and exc.response is not None
                    and exc.response.status_code not in {429, 500, 502, 503, 504}
                ):
                    time.sleep(min(0.5 * 2**attempt, 2.0))
                    continue
                break
        status = getattr(getattr(last_error, "response", None), "status_code", "network")
        raise KalshiAPIError(f"Kalshi {method.upper()} {endpoint} failed ({status})") from last_error

    def get_markets(self, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/markets", params=params)

    def get_series_list(self, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/series", params=params)

    def get_market(self, ticker: str) -> dict[str, Any]:
        return self.request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 0) -> dict[str, Any]:
        return self.request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_balance(self) -> dict[str, Any]:
        return self.request("GET", "/portfolio/balance", authenticated=True)

    def get_positions(self) -> dict[str, Any]:
        return self.request("GET", "/portfolio/positions", authenticated=True)

    def get_orders(self, **params: Any) -> dict[str, Any]:
        return self.request("GET", "/portfolio/orders", params=params, authenticated=True)

    def create_order(
        self,
        *,
        ticker: str,
        side: str,
        action: str,
        price_cents: int,
        contracts: int,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        if side not in {"yes", "no"} or action not in {"buy", "sell"}:
            raise ValueError("invalid side/action")
        if not 1 <= price_cents <= 99 or contracts <= 0:
            raise ValueError("invalid order price/count")
        body: dict[str, Any] = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": "limit",
            "count": contracts,
            "client_order_id": client_order_id or str(uuid.uuid4()),
            f"{side}_price": price_cents,
        }
        return self.request("POST", "/portfolio/orders", json_body=body, authenticated=True)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/portfolio/orders/{order_id}", authenticated=True)
