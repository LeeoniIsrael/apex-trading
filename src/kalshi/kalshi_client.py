"""
Kalshi REST API v2 client with RSA authentication.
Paper mode logs instead of executing orders.
"""
import base64
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEMO_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    def __init__(
        self,
        key_id: str,
        private_key_path: str,
        paper_mode: bool = True,
        base_url: Optional[str] = None,
    ):
        self.key_id = key_id
        self.paper_mode = paper_mode
        self.base_url = base_url or (DEMO_BASE_URL if paper_mode else PROD_BASE_URL)
        # Extract the path prefix (e.g. "/trade-api/v2") for signing
        self._base_path = urlparse(self.base_url).path.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        # Load private key
        key_path = Path(private_key_path)
        if not key_path.exists():
            raise FileNotFoundError(f"Kalshi private key not found: {private_key_path}")

        key_content = key_path.read_bytes()
        if b"RSA KEY WILL BE PASTED" in key_content:
            raise ValueError(
                "kalshi_private.pem contains placeholder. Paste your RSA key first."
            )

        self.private_key = serialization.load_pem_private_key(key_content, password=None)

    def _sign(self, timestamp_ms: str, method: str, full_path: str) -> str:
        """
        RSA-PSS-SHA256 signature per Kalshi spec.
        Message = timestamp_ms (string) + HTTP_METHOD (uppercase) + full_path (no query string).
        """
        message = f"{timestamp_ms}{method}{full_path}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _headers(self, method: str, endpoint: str) -> dict:
        """
        Build Kalshi auth headers.
        endpoint: path relative to base_url, e.g. '/portfolio/balance'
        Signing uses the full path: self._base_path + endpoint
        """
        ts = str(int(time.time() * 1000))
        full_path = self._base_path + endpoint  # e.g. /trade-api/v2/portfolio/balance
        sig = self._sign(ts, method.upper(), full_path)
        headers = {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
        }
        logger.debug(
            "auth | method=%s full_path=%s ts=%s key_id=%s sig_prefix=%s",
            method.upper(), full_path, ts, self.key_id, sig[:16],
        )
        return headers

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        headers = self._headers("GET", path)
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code == 401:
            logger.error(
                "401 on GET %s — headers sent: %s — response: %s",
                url, {k: v for k, v in headers.items()}, resp.text[:300],
            )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        headers = self._headers("POST", path)
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, headers=headers, json=body, timeout=15)
        if resp.status_code == 401:
            logger.error(
                "401 on POST %s — headers sent: %s — response: %s",
                url, {k: v for k, v in headers.items()}, resp.text[:300],
            )
        resp.raise_for_status()
        return resp.json()

    # ── Public API ───────────────────────────────────────────────────────────

    # Short-term series with daily/weekly markets (discovered via probe 2026-03-18)
    _SHORT_TERM_SERIES = [
        "KXBTC", "KXETH", "KXNBAGAME", "KXNFLGAME",
        "FEDHIKE", "CPI", "CPICORE", "INXD", "NFJOBS",
    ]

    def get_markets(self, limit: int = 20, status: str = "open") -> list[dict]:
        """
        Fetch top liquid markets from two sources:
          1. /events endpoint — KX long-form prediction markets
          2. Direct series fetch — short-term daily/weekly markets (crypto, sports, econ)

        /markets (unfiltered) only returns KXMVE zero-volume parlays so we avoid it.
        """
        all_markets: list[dict] = []
        seen_tickers: set[str] = set()

        def _add(markets_list: list[dict], event_title: str = "", event_category: str = "") -> None:
            for m in markets_list:
                t = m.get("ticker", "")
                if t and t not in seen_tickers:
                    seen_tickers.add(t)
                    if event_title:
                        m["_event_title"] = event_title
                    if event_category:
                        m["_event_category"] = event_category
                    all_markets.append(m)

        # ── Source 1: /events → KX long-form universe, cycled by category ──────
        # Keywords chosen to prioritise same-day resolution markets
        _CATEGORIES = [
            "sports", "crypto", "economics", "politics",
            "bitcoin", "fed", "inflation", "weather",
            "nfl", "nba", "nhl", "mlb", "soccer",
        ]
        for category in _CATEGORIES:
            try:
                events_data = self._get("/events", params={
                    "limit": 15, "status": status, "category": category,
                })
                for event in events_data.get("events", [])[:5]:
                    try:
                        detail = self._get(f"/events/{event['event_ticker']}")
                        _add(detail.get("markets", []),
                             event.get("title", ""), event.get("category", category))
                    except Exception as e:
                        logger.debug("Failed to fetch event %s: %s", event.get("event_ticker"), e)
            except Exception as e:
                logger.warning("Failed to fetch /events?category=%s: %s", category, e)

        # ── Source 2: known short-term series ─────────────────────────────────
        for series in self._SHORT_TERM_SERIES:
            try:
                d = self._get("/markets", params={"limit": 50, "status": status,
                                                  "series_ticker": series})
                _add(d.get("markets", []))
            except Exception as e:
                logger.debug("Series %s fetch failed: %s", series, e)

        def _vol(m: dict) -> float:
            try:
                return float(m.get("volume_fp") or 0)
            except (ValueError, TypeError):
                return 0.0

        sorted_markets = sorted(all_markets, key=_vol, reverse=True)
        logger.info(
            "get_markets: %d total (events+series), top 5 volumes: %s",
            len(all_markets),
            [(m.get("ticker", "")[:35], m.get("volume_fp", "0")) for m in sorted_markets[:5]],
        )
        return sorted_markets[:limit]

    @staticmethod
    def yes_price_cents(market: dict) -> int:
        """Extract YES ask price as integer cents (0–99) from market dict."""
        # API returns prices as dollar strings: "0.4500" → 45 cents
        for field in ("yes_ask_dollars", "yes_ask", "last_price_dollars"):
            val = market.get(field)
            if val is not None:
                try:
                    cents = round(float(val) * 100)
                    if 1 <= cents <= 99:
                        return cents
                except (ValueError, TypeError):
                    pass
        return 50  # fallback: 50/50

    def get_market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}")

    def get_balance(self) -> dict:
        return self._get("/portfolio/balance")

    def get_positions(self) -> list[dict]:
        data = self._get("/portfolio/positions")
        return data.get("market_positions", [])

    def get_orderbook(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}/orderbook")

    def place_order(
        self,
        ticker: str,
        side: str,       # "yes" or "no"
        amount_cents: int,
        price_cents: int,
    ) -> dict:
        """
        Place a limit order. In paper mode, logs and returns a mock response.
        amount_cents: dollar amount * 100
        price_cents:  price per contract (0–99 cents)
        """
        order: dict = {
            "ticker": ticker,
            "side": side.lower(),
            "action": "buy",
            "type": "limit",
            "count": amount_cents // 100,  # contracts = dollars (each contract costs $1 max)
        }
        # Kalshi requires exactly one price field
        if side.lower() == "yes":
            order["yes_price"] = price_cents
        else:
            order["no_price"] = price_cents

        if self.paper_mode:
            logger.info(
                "[PAPER] place_order | ticker=%s side=%s amount_cents=%s price_cents=%s",
                ticker, side, amount_cents, price_cents,
            )
            return {
                "order": {
                    "order_id": f"PAPER-{int(time.time())}",
                    "ticker": ticker,
                    "side": side,
                    "status": "resting",
                    "created_time": datetime.now(timezone.utc).isoformat(),
                    **order,
                }
            }

        return self._post("/portfolio/orders", order)

    def place_limit_order(
        self,
        ticker: str,
        side: str,        # "yes" or "no"
        price_cents: int, # maker limit price (0–99)
        contracts: int,   # number of contracts
    ) -> dict:
        """
        Place a maker limit order by contract count (not dollar amount).
        Used by weather_strategy and longshot_fade for tighter spread control.
        """
        order: dict = {
            "ticker": ticker,
            "side":   side.lower(),
            "action": "buy",
            "type":   "limit",
            "count":  contracts,
        }
        # Kalshi requires exactly one price field
        if side.lower() == "yes":
            order["yes_price"] = price_cents
        else:
            order["no_price"] = price_cents

        if self.paper_mode:
            logger.info(
                "[PAPER] place_limit_order | ticker=%s side=%s price=%s¢ contracts=%s",
                ticker, side, price_cents, contracts,
            )
            return {
                "order": {
                    "order_id":    f"PAPER-LIMIT-{int(time.time())}",
                    "ticker":      ticker,
                    "side":        side,
                    "status":      "resting",
                    "created_time": datetime.now(timezone.utc).isoformat(),
                    **order,
                }
            }

        return self._post("/portfolio/orders", order)
