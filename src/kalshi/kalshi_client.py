"""
Kalshi REST API v2 client with RSA authentication.
Paper mode logs instead of executing orders.
"""
import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        # Load private key
        key_path = Path(private_key_path)
        if not key_path.exists():
            raise FileNotFoundError(f"Kalshi private key not found: {private_key_path}")

        key_content = key_path.read_bytes()
        # Skip placeholder comments
        if b"RSA KEY WILL BE PASTED" in key_content:
            raise ValueError(
                "kalshi_private.pem contains placeholder. Paste your RSA key first."
            )

        self.private_key = serialization.load_pem_private_key(key_content, password=None)

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        """Create RSA-SHA256 signature for Kalshi API auth."""
        message = f"{timestamp_ms}{method}{path}".encode()
        signature = self.private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode()

    def _headers(self, method: str, path: str) -> dict:
        ts = str(int(time.time() * 1000))
        return {
            "Kalshi-Access-Key": self.key_id,
            "Kalshi-Access-Timestamp": ts,
            "Kalshi-Access-Signature": self._sign(ts, method.upper(), path),
        }

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        headers = self._headers("GET", path)
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        headers = self._headers("POST", path)
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, headers=headers, json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ── Public API ───────────────────────────────────────────────────────────

    def get_markets(self, limit: int = 20, status: str = "open") -> list[dict]:
        """Fetch top markets sorted by volume."""
        data = self._get("/markets", params={"limit": limit, "status": status})
        markets = data.get("markets", [])
        # Sort by volume descending
        return sorted(markets, key=lambda m: m.get("volume", 0), reverse=True)

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
        order = {
            "ticker": ticker,
            "side": side.lower(),
            "action": "buy",
            "type": "limit",
            "count": amount_cents // 100,  # contracts = dollars (each contract costs $1 max)
            "yes_price": price_cents if side.lower() == "yes" else 100 - price_cents,
            "no_price": price_cents if side.lower() == "no" else 100 - price_cents,
        }

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
