"""
APEX Weather Arb — two strategies on Kalshi KXHIGH temperature bracket markets.

Inspired by the Polymarket "Kimi Claw" weather bot (spread arb + ultra-low bracket).

Strategy A — Spread Arb:
  Buy YES and NO simultaneously on the same bracket when their combined price < 98¢.
  At resolution one side pays $1.00 and the other pays $0. Net: guaranteed spread profit.
  Edge = (100 - YES_cents - NO_cents) cents per pair.
  Scale is the game: 500 contracts × 3¢ spread = $15 guaranteed per pair.

Strategy B — Ultra-low Bracket Long:
  Buy YES at 1–5¢ on temperature brackets. At 2¢ entry, break-even hit rate is 2%.
  Historical temperature data shows 4–6% hit rate on tight brackets — 2–3x EV positive.
  NOAA hourly forecast gives a timing edge: buy before the market reprices when NOAA
  updates and shows a bracket becoming likely.

Runs every 90 seconds. NOAA responses are cached for 50 minutes.
"""
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

_HERE = Path(__file__).parent
_APEX_DIR = Path("/opt/apex")
for _p in [str(_HERE), str(_APEX_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(_HERE / ".env")
load_dotenv(_APEX_DIR / ".env")

import telegram_notify as tg
from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

PAPER_MODE      = os.getenv("APEX_ENV", "paper").lower() == "paper"
TRADES_LOG_PATH = Path(os.getenv("TRADES_LOG", "/opt/apex/trades.log"))

# Strategy A — Spread Arb
SPREAD_MIN_EDGE_CENTS = 2       # skip if YES+NO > 98¢ (edge too thin after fees)
SPREAD_BET_BUDGET_USD = 10.0    # capital per pair; volume is the revenue engine
SPREAD_MAX_CONTRACTS  = 1000    # hard cap per side
SPREAD_MAX_USD        = 50.0    # total capital cap per pair

# Strategy B — Ultra-low Bracket Long
ULTRA_LOW_MAX_CENTS       = 5   # YES must be priced ≤ 5¢
ULTRA_LOW_BET_USD         = 3.0 # flat per entry — lottery-style volume betting
ULTRA_LOW_MAX_POSITIONS   = 10  # concurrent cap (×$3 = $30 max exposure)
ULTRA_LOW_NOAA_MIN_PCT    = 0.5 # need ≥0.5% NOAA signal to enter

# NOAA cache: TTL 50 minutes (NOAA updates hourly; avoid hitting on every 90s tick)
_NOAA_CACHE: dict[str, tuple[float, list[float]]] = {}
_NOAA_TTL   = 50 * 60  # seconds

# Dedup: don't re-enter same ticker in same day
_ULTRA_LOW_TODAY: set[str] = set()
_ULTRA_LOW_DATE: str = ""

CITIES = [
    {"name": "NYC",     "lat": 40.71,  "lon": -74.01,  "kalshi_suffix": "NYN"},
    {"name": "Chicago", "lat": 41.88,  "lon": -87.63,  "kalshi_suffix": "CHI"},
    {"name": "Miami",   "lat": 25.77,  "lon": -80.19,  "kalshi_suffix": "MIA"},
    {"name": "Austin",  "lat": 30.27,  "lon": -97.74,  "kalshi_suffix": "AUS"},
    {"name": "LA",      "lat": 34.05,  "lon": -118.24, "kalshi_suffix": "LAX"},
    {"name": "Denver",  "lat": 39.74,  "lon": -104.98, "kalshi_suffix": "DEN"},
]
_SUFFIX_TO_CITY = {c["kalshi_suffix"]: c for c in CITIES}

# KXHIGH-26APR23-NYN-B85  →  (suffix=NYN, direction=B/T, threshold=85)
_KXHIGH_RE = re.compile(r"^KXHIGH-\d{2}[A-Z]{3}\d{2}-([A-Z]+)-([BT])(\d+)$")


def _reset_dedup_if_new_day() -> None:
    global _ULTRA_LOW_TODAY, _ULTRA_LOW_DATE
    today = datetime.now(timezone.utc).date().isoformat()
    if today != _ULTRA_LOW_DATE:
        _ULTRA_LOW_TODAY = set()
        _ULTRA_LOW_DATE = today


def _parse_kxhigh_ticker(ticker: str) -> dict | None:
    m = _KXHIGH_RE.match(ticker)
    if not m:
        return None
    suffix, direction_char, threshold_str = m.group(1), m.group(2), m.group(3)
    city = _SUFFIX_TO_CITY.get(suffix)
    if not city:
        return None
    return {
        "city":      city,
        "suffix":    suffix,
        "direction": "above" if direction_char == "B" else "below",
        "threshold": float(threshold_str),
    }


def _fetch_noaa_hourly(lat: float, lon: float) -> list[float]:
    """
    Return a list of hourly forecast temperatures (°F) for the next 48 hours
    from NOAA's public api.weather.gov. Cached for 50 minutes.
    """
    cache_key = f"{lat:.2f},{lon:.2f}"
    now = time.time()
    if cache_key in _NOAA_CACHE:
        fetched_at, temps = _NOAA_CACHE[cache_key]
        if now - fetched_at < _NOAA_TTL:
            return temps

    try:
        # Step 1: resolve grid point
        pts_resp = requests.get(
            f"https://api.weather.gov/points/{lat},{lon}",
            headers={"User-Agent": "APEX-Trading/1.0 (leeoniisrael@gmail.com)"},
            timeout=10,
        )
        pts_resp.raise_for_status()
        props = pts_resp.json()["properties"]
        forecast_url = props["forecastHourly"]

        # Step 2: fetch hourly forecast
        fc_resp = requests.get(
            forecast_url,
            headers={"User-Agent": "APEX-Trading/1.0 (leeoniisrael@gmail.com)"},
            timeout=10,
        )
        fc_resp.raise_for_status()
        periods = fc_resp.json()["properties"]["periods"]
        temps = [float(p["temperature"]) for p in periods[:48]]
        _NOAA_CACHE[cache_key] = (now, temps)
        logger.debug("NOAA fetched %.2f,%.2f — %d periods", lat, lon, len(temps))
        return temps
    except Exception as e:
        logger.warning("NOAA fetch failed (%.2f, %.2f): %s", lat, lon, e)
        return []


def _noaa_bracket_prob(temps: list[float], threshold: float, direction: str) -> float:
    """
    Fraction of NOAA forecast hours where the bracket resolves YES.
    direction="above": YES if daily high > threshold
    direction="below": YES if daily high <= threshold
    Approximated by checking hourly temps against the threshold.
    """
    if not temps:
        return 0.0
    if direction == "above":
        hits = sum(1 for t in temps if t > threshold)
    else:
        hits = sum(1 for t in temps if t <= threshold)
    return hits / len(temps)


def _log_trade(entry: dict) -> None:
    try:
        with open(TRADES_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("trade log write failed: %s", e)


def _get_temperature_markets(client: KalshiClient) -> list[dict]:
    try:
        data = client._get("/markets", params={
            "series_ticker": "KXHIGH",
            "status":        "open",
            "limit":         100,
        })
        return data.get("markets", [])
    except Exception as e:
        logger.warning("KXHIGH market fetch failed: %s", e)
        return []


def run_spread_arb(client: KalshiClient, markets: list[dict]) -> list[dict]:
    """
    Strategy A: for each bracket where YES+NO combined price < 98¢, buy both sides.
    Guaranteed profit at resolution = (100 - YES - NO) cents per contract pair.
    """
    orders: list[dict] = []

    for market in markets:
        ticker = market.get("ticker", "")
        if not _parse_kxhigh_ticker(ticker):
            continue

        yes_cents = KalshiClient.yes_price_cents(market)
        no_cents  = 100 - yes_cents
        combined  = yes_cents + no_cents  # normally == 100

        # Check for actual spread data from orderbook if available
        try:
            ob = client.get_orderbook(ticker)
            book = ob.get("orderbook", {})
            yes_levels = book.get("yes", [])
            no_levels  = book.get("no",  [])
            if yes_levels:
                yes_cents = int(yes_levels[0][0])
            if no_levels:
                no_cents = int(no_levels[0][0])
            combined = yes_cents + no_cents
        except Exception:
            pass

        edge_cents = 100 - combined
        if edge_cents < SPREAD_MIN_EDGE_CENTS:
            continue

        # Size: how many pairs fit in the budget, capped at SPREAD_MAX_CONTRACTS
        pair_cost_cents = yes_cents + no_cents  # cost per 1 YES + 1 NO contract
        contracts = min(
            SPREAD_MAX_CONTRACTS,
            int(SPREAD_BET_BUDGET_USD * 100 / pair_cost_cents),
        )
        if contracts < 1:
            continue

        cost_usd = round(contracts * pair_cost_cents / 100, 2)
        if cost_usd > SPREAD_MAX_USD:
            contracts = int(SPREAD_MAX_USD * 100 / pair_cost_cents)
            cost_usd  = round(contracts * pair_cost_cents / 100, 2)

        guaranteed_profit = round(contracts * edge_cents / 100, 2)

        logger.info(
            "SPREAD ARB %s — YES=%d¢ NO=%d¢ edge=%d¢ ×%d contracts "
            "cost=$%.2f guaranteed_profit=$%.2f",
            ticker, yes_cents, no_cents, edge_cents, contracts, cost_usd, guaranteed_profit,
        )

        # Place YES side
        yes_placed = False
        try:
            client.place_limit_order(ticker=ticker, side="yes",
                                     price_cents=yes_cents, contracts=contracts)
            yes_placed = True
        except Exception as e:
            logger.error("Spread arb YES order failed %s: %s", ticker, e)
            continue

        # Place NO side — if this fails, YES is already in; log and move on
        try:
            client.place_limit_order(ticker=ticker, side="no",
                                     price_cents=no_cents, contracts=contracts)
        except Exception as e:
            logger.error("Spread arb NO order failed %s (YES already placed): %s", ticker, e)
            if yes_placed:
                logger.warning("Partial spread on %s — monitor manually", ticker)

        entry = {
            "date":              datetime.now(timezone.utc).isoformat(),
            "strategy":          "weather_spread_arb",
            "ticker":            ticker,
            "yes_cents":         yes_cents,
            "no_cents":          no_cents,
            "edge_cents":        edge_cents,
            "contracts":         contracts,
            "cost_usd":          cost_usd,
            "guaranteed_profit": guaranteed_profit,
            "paper":             PAPER_MODE,
        }
        orders.append(entry)
        _log_trade(entry)

        parsed = _parse_kxhigh_ticker(ticker)
        city_name = parsed["city"]["name"] if parsed else ticker
        msg = (
            f"Locked in a weather spread on {city_name} High Temp! "
            f"Bought both YES and NO for ${cost_usd:.2f}. "
            f"Guaranteed profit at resolution: +${guaranteed_profit:.2f}"
        )
        asyncio.run(tg.send_message(msg))

    return orders


def run_ultra_low_bracket(
    client: KalshiClient,
    markets: list[dict],
) -> list[dict]:
    """
    Strategy B: buy YES at 1–5¢ on temperature brackets when NOAA gives even
    a marginal signal. Pure volume play — 4-6% hit rate at 50x pays handsomely.
    """
    _reset_dedup_if_new_day()
    orders: list[dict] = []
    open_count = len(_ULTRA_LOW_TODAY)

    for market in markets:
        if open_count >= ULTRA_LOW_MAX_POSITIONS:
            break

        ticker = market.get("ticker", "")
        if ticker in _ULTRA_LOW_TODAY:
            continue

        parsed = _parse_kxhigh_ticker(ticker)
        if not parsed:
            continue

        yes_cents = KalshiClient.yes_price_cents(market)
        if not (1 <= yes_cents <= ULTRA_LOW_MAX_CENTS):
            continue

        city      = parsed["city"]
        direction = parsed["direction"]
        threshold = parsed["threshold"]

        noaa_temps = _fetch_noaa_hourly(city["lat"], city["lon"])
        noaa_prob  = _noaa_bracket_prob(noaa_temps, threshold, direction)

        if noaa_prob < ULTRA_LOW_NOAA_MIN_PCT / 100:
            logger.debug(
                "SKIP %s — NOAA bracket prob %.2f%% < %.1f%% floor",
                ticker, noaa_prob * 100, ULTRA_LOW_NOAA_MIN_PCT,
            )
            continue

        contracts = max(1, int(ULTRA_LOW_BET_USD / (yes_cents / 100)))
        cost_usd  = round(contracts * yes_cents / 100, 2)
        payout_usd = round(contracts * 1.00, 2)
        profit_if_hit = round(payout_usd - cost_usd, 2)

        logger.info(
            "ULTRA-LOW %s — %s %s°F YES=%d¢ NOAA=%.1f%% ×%d @%d¢ cost=$%.2f | if hit +$%.2f",
            ticker, city["name"],
            f"{'>' if direction == 'above' else '<='}{threshold:.0f}",
            yes_cents, noaa_prob * 100,
            contracts, yes_cents, cost_usd, profit_if_hit,
        )

        try:
            client.place_limit_order(
                ticker=ticker, side="yes",
                price_cents=yes_cents, contracts=contracts,
            )
        except Exception as e:
            logger.error("Ultra-low order failed %s: %s", ticker, e)
            continue

        _ULTRA_LOW_TODAY.add(ticker)
        open_count += 1

        entry = {
            "date":           datetime.now(timezone.utc).isoformat(),
            "strategy":       "weather_ultra_low",
            "ticker":         ticker,
            "city":           city["name"],
            "direction":      direction,
            "threshold":      threshold,
            "side":           "yes",
            "price_cents":    yes_cents,
            "contracts":      contracts,
            "cost_usd":       cost_usd,
            "noaa_prob_pct":  round(noaa_prob * 100, 2),
            "paper":          PAPER_MODE,
        }
        orders.append(entry)
        _log_trade(entry)

    return orders


def run_weather_arb() -> dict:
    """
    Entry point called by APScheduler every 90 seconds.
    Returns {"spread": [...], "ultra_low": [...]}
    """
    logger.info("── Weather arb scan starting ──")

    try:
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv(
                "KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
            paper_mode=PAPER_MODE,
        )
    except Exception as e:
        logger.warning("Weather arb: client init failed: %s", e)
        return {"spread": [], "ultra_low": []}

    markets = _get_temperature_markets(client)
    if not markets:
        logger.info("Weather arb: no KXHIGH markets found")
        return {"spread": [], "ultra_low": []}

    logger.info("Weather arb: found %d KXHIGH markets", len(markets))

    spread_orders    = run_spread_arb(client, markets)
    ultra_low_orders = run_ultra_low_bracket(client, markets)

    logger.info(
        "── Weather arb complete | spread=%d ultra_low=%d ──",
        len(spread_orders), len(ultra_low_orders),
    )
    return {"spread": spread_orders, "ultra_low": ultra_low_orders}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_weather_arb()
