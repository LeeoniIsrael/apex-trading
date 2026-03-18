"""
Simple read-only wrapper for the Polymarket Gamma API.
No auth required for public market data.
"""
import logging

import requests

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
TIMEOUT = 10


class PolymarketClient:
    def get_events(self, limit: int = 100) -> list[dict]:
        """Fetch active, open events ordered by volume."""
        try:
            resp = requests.get(
                f"{GAMMA_BASE}/events",
                params={"active": "true", "closed": "false", "limit": limit},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error("get_events failed: %s", e)
            return []

    def get_market(self, market_id: str) -> dict:
        """Fetch a single market by ID."""
        try:
            resp = requests.get(
                f"{GAMMA_BASE}/markets/{market_id}",
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("get_market(%s) failed: %s", market_id, e)
            return {}

    def get_trades(self, limit: int = 50) -> list[dict]:
        """Fetch recent trades."""
        try:
            resp = requests.get(
                f"{GAMMA_BASE}/trades",
                params={"limit": limit},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error("get_trades failed: %s", e)
            return []
