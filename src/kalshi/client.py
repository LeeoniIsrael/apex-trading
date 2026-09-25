"""Timeout/retry-aware Kalshi REST client with idempotent order creation."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
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
        attempts = 1 if method.upper() in {"POST", "PUT"} else self.max_attempts
        for attempt in range(attempts):
            headers = self.signer.headers(method, path) if authenticated and self.signer else {}
            try:
                response = self.session.request(
                    method, f"{self.base_url}{endpoint}", params=params, json=json_body,
                    headers=headers, timeout=self.timeout_seconds,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(min(0.5 * 2**attempt, 2.0))
                    continue
                response.raise_for_status()
                return response.json()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
                if attempt + 1 < attempts and not (
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
        # V2 quotes the YES leg: buying NO at 30c is an ask at YES 70c.
        yes_price = price_cents if side == 'yes' else 100-price_cents
        book_side = 'bid' if (action,side) in {('buy','yes'),('sell','no')} else 'ask'
        body: dict[str, Any] = {
            'ticker': ticker, 'side': book_side, 'count': f'{contracts:.2f}',
            'price': f'{Decimal(yes_price)/100:.4f}',
            'client_order_id': client_order_id or str(uuid.uuid4()),
            'time_in_force': 'immediate_or_cancel',
            'self_trade_prevention_type': 'taker_at_cross',
            'cancel_order_on_pause': True,
        }
        result = self.request('POST','/portfolio/events/orders',json_body=body,authenticated=True)
        filled=Decimal(str(result['fill_count']))
        remaining=Decimal(str(result['remaining_count']))
        if (not filled.is_finite() or not remaining.is_finite() or filled < 0 or remaining < 0
            or filled != filled.to_integral_value() or remaining != remaining.to_integral_value()
            or filled+remaining > contracts):
            raise KalshiAPIError('fractional or invalid execution requires reconciliation')
        status='executed' if filled else 'cancelled' if not remaining else 'resting'
        return {'order':{**result,'status':status,'fill_count':int(filled)}}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/portfolio/orders/{order_id}", authenticated=True)
