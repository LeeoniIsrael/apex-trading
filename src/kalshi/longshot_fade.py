"""
APEX Longshot Fade Strategy.

Exploits the favourite-longshot bias on Kalshi: contracts priced 5–20¢ (YES)
win far less often than their implied probability suggests. We systematically
buy NO on these longshots, targeting a structural edge of ~10–15¢ per contract.

Scan every 30 minutes. Buy NO on any open market where:
  - YES price is between 5¢ and 20¢  (longshot zone)
  - Volume ≥ 50 contracts             (some liquidity)
  - Hours until close: 1–24h          (same-day resolution)
  - We haven't already faded this ticker today
"""
import json
import logging
import os
import re
import sys
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

import asyncio
import kelly
import telegram_notify as tg
from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

BANKROLL        = float(os.getenv("APEX_BANKROLL", "150.0"))
PAPER_MODE      = os.getenv("APEX_ENV", "paper").lower() == "paper"
KELLY_FRACTION  = 0.20          # Conservative — structural edge only
MAX_BET_USD     = 10.0
TRADES_LOG_PATH = Path(os.getenv("TRADES_LOG", "/opt/apex/trades.log"))

# Longshot zone: YES price in [10, 20] cents.
# We skip the 5-9¢ range: at those extremes the market tends to be correct
# (true long shots that almost never resolve YES). The structural bias is
# strongest in the 10-20¢ band where retail bettors systematically overweight
# small probabilities — these contracts win ~half as often as implied.
LONGSHOT_LOW  = 10
LONGSHOT_HIGH = 20

# Implied true probability adjustment — bias research shows 15¢ YES contracts
# win ~8% of the time vs 15% implied. We model NO true prob = 0.90 (vs 0.85 implied).
# Conservative adjustment: add 5pp to NO side.
BIAS_ADJUSTMENT = 0.05

MIN_VOLUME     = 50
MIN_HOURS      = 1.0
MAX_HOURS      = 24.0

# Dedup: avoid re-fading the same ticker in the same run
_FADED_TODAY: set[str] = set()
_FADE_DATE: str = ""


def _reset_faded_if_new_day() -> None:
    global _FADED_TODAY, _FADE_DATE
    today = datetime.now(timezone.utc).date().isoformat()
    if today != _FADE_DATE:
        _FADED_TODAY = set()
        _FADE_DATE = today


def _hours_until_close(close_str: str) -> float:
    try:
        close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        return (close - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        return 0.0


def _log_trade(entry: dict) -> None:
    try:
        with open(TRADES_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("trade log write failed: %s", e)


def run_longshot_scan() -> list[dict]:
    """
    Entry point called by APScheduler every 30 minutes.
    Returns list of orders placed.
    """
    _reset_faded_if_new_day()
    logger.info("── Longshot fade scan starting ──")

    try:
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv(
                "KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
            paper_mode=PAPER_MODE,
        )
    except Exception as e:
        logger.warning("Longshot scan: Kalshi client init failed: %s", e)
        return []

    # Fetch a broad slice of open markets
    try:
        markets = client.get_markets(limit=50)
    except Exception as e:
        logger.warning("Longshot scan: market fetch failed: %s", e)
        return []

    orders: list[dict] = []

    for market in markets:
        ticker = market.get("ticker", "")
        if ticker in _FADED_TODAY:
            continue

        yes_price = KalshiClient.yes_price_cents(market)
        if not (LONGSHOT_LOW <= yes_price <= LONGSHOT_HIGH):
            continue

        try:
            volume = float(market.get("volume_fp") or market.get("volume", 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0
        if volume < MIN_VOLUME:
            continue

        close_time = (market.get("expected_expiration_time")
                      or market.get("close_time", ""))
        hours_left = _hours_until_close(close_time)
        if not (MIN_HOURS <= hours_left <= MAX_HOURS):
            continue

        title = market.get("_event_title") or market.get("title", ticker)

        # Edge calculation:
        # Market implies NO probability = 1 - yes_price/100
        # We believe true NO probability = market NO + BIAS_ADJUSTMENT
        market_no_p  = 1.0 - yes_price / 100.0      # e.g. 0.85 for 15¢ YES
        our_no_p     = market_no_p + BIAS_ADJUSTMENT  # e.g. 0.90
        edge         = our_no_p - market_no_p         # always = BIAS_ADJUSTMENT

        no_price_cents = 100 - yes_price

        # Use maker price from orderbook if available
        limit_price = no_price_cents
        try:
            ob = client.get_orderbook(ticker)
            book = ob.get("orderbook", {})
            no_levels = book.get("no", [])
            if no_levels:
                limit_price = int(no_levels[0][0])
        except Exception:
            pass

        bet_usd = kelly.kelly_bet(
            bankroll=BANKROLL,
            our_probability=our_no_p,
            market_probability=market_no_p,
            kelly_fraction=KELLY_FRACTION,
            max_pct=0.07,
        )
        bet_usd   = min(max(bet_usd, 1.0), MAX_BET_USD)
        contracts = max(1, int(bet_usd))

        logger.info(
            "LONGSHOT FADE %s — YES=%d¢ NO=%d¢ vol=%.0f hours=%.1fh → BUY NO x%d @ %d¢",
            ticker, yes_price, no_price_cents, volume, hours_left, contracts, limit_price,
        )

        try:
            result = client.place_limit_order(
                ticker=ticker, side="no",
                price_cents=limit_price, contracts=contracts,
            )
        except Exception as e:
            logger.error("Longshot order failed %s: %s", ticker, e)
            continue

        _FADED_TODAY.add(ticker)
        cost_usd = round(contracts * limit_price / 100, 2)

        entry = {
            "date":         datetime.now(timezone.utc).isoformat(),
            "strategy":     "longshot_fade",
            "ticker":       ticker,
            "title":        title,
            "yes_price":    yes_price,
            "no_price":     no_price_cents,
            "market_no_p":  round(market_no_p, 4),
            "our_no_p":     round(our_no_p, 4),
            "edge":         round(edge, 4),
            "side":         "no",
            "price_cents":  limit_price,
            "contracts":    contracts,
            "cost_usd":     cost_usd,
            "paper":        PAPER_MODE,
            "order_id":     result.get("order", {}).get("order_id", ""),
        }
        orders.append(entry)
        _log_trade(entry)

        msg = (
            f"*LONGSHOT FADE:* {title[:60]} — "
            f"YES at {yes_price}¢ (longshot), buying NO at {limit_price}¢, "
            f"edge +{edge:.0%} structural bias — ${cost_usd:.2f} placed"
        )
        logger.info(msg.replace("*", ""))
        asyncio.run(tg.send_message(msg))

    logger.info(
        "── Longshot scan complete | checked=%d faded=%d orders=%d ──",
        len(markets), len(_FADED_TODAY), len(orders),
    )
    return orders


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_longshot_scan()
