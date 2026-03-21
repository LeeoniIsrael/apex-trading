"""
APEX Weather Model Edge Strategy.

Fetches GFS ensemble forecasts from Open-Meteo API (free, no key required)
and compares predicted temperature distributions to Kalshi bracket prices.

A structural edge exists when the ensemble model disagrees with the market
by 8%+ on a temperature bracket's probability.

Run every 6 hours (aligned with GFS model updates: 00Z, 06Z, 12Z, 18Z UTC).
"""
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Support both local src/kalshi/ layout and flat /opt/apex/ server layout
_HERE = Path(__file__).parent
_APEX_DIR = Path("/opt/apex")
for _p in [str(_HERE), str(_APEX_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(_HERE / ".env")
load_dotenv(_APEX_DIR / ".env")

import kelly
import telegram_notify as tg
from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

BANKROLL        = float(os.getenv("APEX_BANKROLL", "150.0"))
PAPER_MODE      = os.getenv("APEX_ENV", "paper").lower() == "paper"
KELLY_FRACTION  = 0.15
MAX_BET_USD     = 15.0
EDGE_THRESHOLD  = 0.08   # 8% minimum model vs market gap
TRADES_LOG_PATH = Path(os.getenv("TRADES_LOG", "/opt/apex/trades.log"))

OPEN_METEO_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

CITIES = [
    {"name": "NYC",     "lat": 40.71,  "lon": -74.01,  "suffix": "NYN"},
    {"name": "Chicago", "lat": 41.88,  "lon": -87.63,  "suffix": "CHI"},
    {"name": "Miami",   "lat": 25.77,  "lon": -80.19,  "suffix": "MIA"},
    {"name": "Austin",  "lat": 30.27,  "lon": -97.74,  "suffix": "AUS"},
    {"name": "LA",      "lat": 34.05,  "lon": -118.24, "suffix": "LAX"},
    {"name": "Denver",  "lat": 39.74,  "lon": -104.98, "suffix": "DEN"},
]


def _fetch_ensemble_highs(lat: float, lon: float) -> list[float]:
    """
    Call Open-Meteo GFS ensemble and return predicted daily HIGH temp (°F)
    for today's calendar date — one value per ensemble member.
    """
    try:
        resp = requests.get(
            OPEN_METEO_URL,
            params={
                "latitude":        lat,
                "longitude":       lon,
                "hourly":          "temperature_2m",
                "models":          "gfs_seamless",
                "forecast_days":   2,
                "temperature_unit":"fahrenheit",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Open-Meteo fetch failed (%.2f, %.2f): %s", lat, lon, e)
        return []

    hourly    = data.get("hourly", {})
    times     = hourly.get("time", [])
    today_str = datetime.now(timezone.utc).date().isoformat()   # "2026-03-18"

    today_idx = [i for i, t in enumerate(times) if t.startswith(today_str)]
    if not today_idx:
        logger.warning("No today data in ensemble response for (%.2f, %.2f)", lat, lon)
        return []

    member_keys = sorted(k for k in hourly if k.startswith("temperature_2m_member"))

    if not member_keys:
        # Deterministic fallback (single forecast, treat as one member)
        vals = hourly.get("temperature_2m", [])
        today_vals = [vals[i] for i in today_idx if i < len(vals) and vals[i] is not None]
        return [max(today_vals)] if today_vals else []

    highs = []
    for key in member_keys:
        member_vals = hourly.get(key, [])
        today_vals  = [member_vals[i] for i in today_idx
                       if i < len(member_vals) and member_vals[i] is not None]
        if today_vals:
            highs.append(max(today_vals))
    return highs


def _parse_bracket(title: str) -> tuple[float, float] | None:
    """
    Extract temperature bracket bounds from a Kalshi market title.
    Returns (low, high) inclusive, or None if not parseable.
    """
    # "72-74" or "72 to 74"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", title)
    if m:
        return float(m.group(1)), float(m.group(2))
    # "above X" / "over X"
    m = re.search(r"(?:above|over)\s*(\d+(?:\.\d+)?)", title, re.IGNORECASE)
    if m:
        return float(m.group(1)), 999.0
    # "below X" / "under X"
    m = re.search(r"(?:below|under)\s*(\d+(?:\.\d+)?)", title, re.IGNORECASE)
    if m:
        return -999.0, float(m.group(1))
    return None


def _model_prob(highs: list[float], low: float, high: float) -> float:
    """Fraction of ensemble members whose predicted high falls within [low, high]."""
    if not highs:
        return 0.5
    return sum(1 for h in highs if low <= h <= high) / len(highs)


def _get_city_markets(client: KalshiClient, suffix: str) -> list[dict]:
    """Fetch open Kalshi markets for the KXHIGH temperature series, filtered by city suffix."""
    try:
        data = client._get("/markets", params={
            "limit": 50, "status": "open", "series_ticker": "KXHIGH",
        })
        return [m for m in data.get("markets", [])
                if suffix.upper() in m.get("ticker", "").upper()]
    except Exception as e:
        logger.debug("Weather market fetch for %s failed: %s", suffix, e)
        return []


def _recently_traded(ticker: str, side: str, hours: int = 24) -> bool:
    """Return True if a trade for this ticker+side appears in trades.log within the last N hours."""
    if not TRADES_LOG_PATH.exists():
        return False
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    try:
        for line in TRADES_LOG_PATH.read_text().splitlines():
            try:
                t = json.loads(line)
                if t.get("ticker") != ticker or t.get("side", "").lower() != side.lower():
                    continue
                trade_time = datetime.fromisoformat(
                    t["date"].replace("Z", "+00:00")
                ).timestamp()
                if trade_time >= cutoff:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _log_trade(entry: dict) -> None:
    try:
        with open(TRADES_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("trade log write failed: %s", e)


def run_weather_scan() -> list[dict]:
    """
    Entry point called by APScheduler every 6 hours.
    Returns list of orders placed.
    """
    logger.info("── Weather strategy scan starting ──")
    try:
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv(
                "KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
            paper_mode=PAPER_MODE,
        )
    except Exception as e:
        logger.warning("Weather scan: Kalshi client init failed: %s", e)
        return []

    orders: list[dict] = []

    for city in CITIES:
        highs = _fetch_ensemble_highs(city["lat"], city["lon"])
        if not highs:
            continue

        median_high = sorted(highs)[len(highs) // 2]
        logger.info(
            "%s — %d members · high range %.1f–%.1f°F · median %.1f°F",
            city["name"], len(highs), min(highs), max(highs), median_high,
        )

        markets = _get_city_markets(client, city["suffix"])
        if not markets:
            logger.info("No active KXHIGH markets for %s (%s)", city["name"], city["suffix"])
            continue

        for market in markets:
            ticker  = market.get("ticker", "")
            title   = market.get("_event_title") or market.get("title", "")
            bracket = _parse_bracket(title)
            if not bracket:
                logger.debug("Cannot parse bracket from title: %s", title)
                continue

            low, high       = bracket
            model_p         = _model_prob(highs, low, high)
            yes_price_cents = KalshiClient.yes_price_cents(market)
            kalshi_p        = yes_price_cents / 100.0
            edge            = model_p - kalshi_p

            logger.info(
                "%s %s — model %.1f%% kalshi %.1f%% edge %+.1f%%",
                ticker, f"{low:.0f}-{high:.0f}F",
                model_p * 100, kalshi_p * 100, edge * 100,
            )

            if abs(edge) < EDGE_THRESHOLD:
                continue

            side = "yes" if edge > 0 else "no"

            if _recently_traded(ticker, side):
                logger.info("SKIP %s — already traded today (trades.log 24h lookback)", ticker)
                continue

            our_p      = model_p        if side == "yes" else (1.0 - model_p)
            market_p   = kalshi_p       if side == "yes" else (1.0 - kalshi_p)
            limit_price = yes_price_cents if side == "yes" else (100 - yes_price_cents)

            # Prefer maker pricing from orderbook
            try:
                ob = client.get_orderbook(ticker)
                book = ob.get("orderbook", {})
                levels = book.get(side, [])
                if levels:
                    limit_price = int(levels[0][0])
            except Exception:
                pass

            bet_usd   = kelly.kelly_bet(
                bankroll=BANKROLL,
                our_probability=our_p,
                market_probability=market_p,
                kelly_fraction=KELLY_FRACTION,
                max_pct=0.10,
            )
            bet_usd   = min(max(bet_usd, 1.0), MAX_BET_USD)
            contracts = max(1, int(bet_usd))

            try:
                result = client.place_limit_order(
                    ticker=ticker, side=side,
                    price_cents=limit_price, contracts=contracts,
                )
            except Exception as e:
                logger.error("Weather order failed %s: %s", ticker, e)
                continue

            cost_usd = round(contracts * limit_price / 100, 2)
            entry = {
                "date":          datetime.now(timezone.utc).isoformat(),
                "strategy":      "weather",
                "ticker":        ticker,
                "title":         title,
                "city":          city["name"],
                "bracket":       f"{low:.0f}-{high:.0f}F",
                "model_prob":    round(model_p, 4),
                "kalshi_prob":   round(kalshi_p, 4),
                "edge":          round(edge, 4),
                "side":          side,
                "price_cents":   limit_price,
                "contracts":     contracts,
                "cost_usd":      cost_usd,
                "paper":         PAPER_MODE,
                "order_id":      result.get("order", {}).get("order_id", ""),
            }
            orders.append(entry)
            _log_trade(entry)

            msg = (
                f"*WEATHER EDGE:* {city['name']} high temp {entry['bracket']}°F bracket — "
                f"model says {model_p:.0%}, market says {kalshi_p:.0%}, "
                f"edge {edge:+.0%} — "
                f"BUY {side.upper()} at {limit_price}¢, ${cost_usd:.2f} limit order placed"
            )
            logger.info(msg.replace("*", ""))
            asyncio.run(tg.send_message(msg))

    logger.info(
        "── Weather scan complete | cities=%d orders=%d ──",
        len(CITIES), len(orders),
    )
    return orders


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_weather_scan()
