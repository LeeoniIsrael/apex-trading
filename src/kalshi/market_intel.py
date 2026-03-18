"""
APEX Market Intelligence — fetches Kalshi, Polymarket top markets, and news RSS.
Runs every 30 minutes via APScheduler. Writes market_intel.json.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

INTEL_PATH = Path(__file__).parent / "market_intel.json"

# Polymarket public API (no auth required)
POLYMARKET_MARKETS_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?limit=20&active=true&closed=false&order=volume24hr&ascending=false"
)

# Working prediction market RSS/Atom feeds
RSS_FEEDS = [
    ("https://www.metaculus.com/questions/rss.xml", "Metaculus"),
    ("https://newsletter.predictionmarkets.news/feed", "PM Newsletter"),
    ("https://forecasting.substack.com/feed", "Forecasting Substack"),
]


def _fetch_kalshi_markets() -> list:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env")
        from kalshi_client import KalshiClient
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
            paper_mode=True,
        )
        markets = client.get_markets(limit=20)
        return [
            {
                "ticker": m.get("ticker", ""),
                "title": m.get("_event_title") or m.get("title", ""),
                "volume": float(m.get("volume_fp") or m.get("volume", 0) or 0),
                "yes_price": KalshiClient.yes_price_cents(m),
                "close_time": m.get("expected_expiration_time") or m.get("close_time", ""),
            }
            for m in markets
        ]
    except Exception as e:
        logger.error("_fetch_kalshi_markets failed: %s", e)
        return []


def _parse_price(outcome_prices) -> float:
    """Parse Polymarket outcomePrices field which may be a list or stringified list."""
    try:
        if isinstance(outcome_prices, list):
            return float(outcome_prices[0])
        if isinstance(outcome_prices, str):
            import ast
            parsed = ast.literal_eval(outcome_prices)
            return float(parsed[0])
    except Exception:
        pass
    return 0.5


def _fetch_polymarket_whales() -> list:
    """Fetch top Polymarket markets by 24h volume as a proxy for whale interest."""
    try:
        import requests
        resp = requests.get(POLYMARKET_MARKETS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        markets = data if isinstance(data, list) else []

        results = []
        for m in markets[:10]:
            vol = float(m.get("volume24hr") or m.get("volume") or 0)
            if vol < 1000:
                continue
            results.append({
                "market": m.get("question", m.get("title", ""))[:80],
                "outcome": "YES",
                "price": _parse_price(m.get("outcomePrices")),
                "size_usd": vol,
                "side": "active",
                "timestamp": m.get("endDate", ""),
            })
        return results[:5]
    except Exception as e:
        logger.error("_fetch_polymarket_whales failed: %s", e)
        return []


def _fetch_news_headlines() -> list:
    try:
        import feedparser
        headlines = []
        for url, source_name in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:4]:
                    title = entry.get("title", "").strip()
                    if not title:
                        continue
                    headlines.append({
                        "title": title,
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "source": source_name,
                    })
            except Exception as e:
                logger.warning("RSS feed %s failed: %s", url, e)
        return headlines[:15]
    except Exception as e:
        logger.error("_fetch_news_headlines failed: %s", e)
        return []


def run_market_intel() -> None:
    logger.info("── Market intelligence scan starting ──")
    now = datetime.now(timezone.utc).isoformat()

    kalshi = _fetch_kalshi_markets()
    whales = _fetch_polymarket_whales()
    news = _fetch_news_headlines()

    intel = {
        "timestamp": now,
        "top_kalshi_markets": kalshi,
        "polymarket_whale_moves": whales,
        "news_headlines": news,
        "generated_at": now,
    }

    INTEL_PATH.write_text(json.dumps(intel, indent=2))
    logger.info(
        "Market intel written | kalshi=%d polymarket=%d headlines=%d",
        len(kalshi), len(whales), len(news),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_market_intel()
