"""
APEX Bitcoin Direction Strategy.

Runs once daily at 8am ET. Combines the Bitcoin Fear & Greed Index with the
24-hour price trend to bet on the daily BTC direction market on Kalshi.

Signal logic:
  - Fear/Greed > 60 (Greed) AND BTC up over last 24h  → BUY YES (BTC higher today)
  - Fear/Greed < 40 (Fear)  AND BTC down over last 24h → BUY NO  (BTC lower today)
  - Signals conflict → SKIP

Fixed $5 limit order per signal. Skips if already traded today.
"""
import asyncio
import json
import logging
import os
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

import telegram_notify as tg
from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

BANKROLL         = float(os.getenv("APEX_BANKROLL", "150.0"))
PAPER_MODE       = os.getenv("APEX_ENV", "paper").lower() == "paper"
BET_USD          = 5.0
TRADES_LOG_PATH  = Path(os.getenv("TRADES_LOG", "/opt/apex/trades.log"))
CASH_RESERVE_PCT = 0.25  # Keep 25% of bankroll as cash reserve (Change 6)

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
)
FNG_URL = "https://api.alternative.me/fng/"

GREED_THRESHOLD = 60
FEAR_THRESHOLD  = 40

_TRADED_TODAY: set[str] = set()
_TRADE_DATE: str = ""


def _reset_if_new_day() -> None:
    global _TRADED_TODAY, _TRADE_DATE
    today = datetime.now(timezone.utc).date().isoformat()
    if today != _TRADE_DATE:
        _TRADED_TODAY = set()
        _TRADE_DATE = today


def _fetch_btc_data() -> tuple[float, float]:
    """Return (current_price_usd, 24h_change_pct)."""
    resp = requests.get(COINGECKO_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()["bitcoin"]
    return float(data["usd"]), float(data["usd_24h_change"])


def _fetch_fear_greed() -> tuple[int, str]:
    """Return (value 0-100, classification label)."""
    resp = requests.get(FNG_URL, timeout=10)
    resp.raise_for_status()
    entry = resp.json()["data"][0]
    return int(entry["value"]), entry["value_classification"]


def _cash_reserve_ok(client: KalshiClient, bet_usd: float) -> bool:
    """Return True if placing bet_usd won't breach the 25% cash reserve (Change 6)."""
    try:
        bal_data = client.get_balance()
        cash_usd = bal_data.get("balance", 0) / 100
        reserve_floor = BANKROLL * CASH_RESERVE_PCT
        if (cash_usd - bet_usd) < reserve_floor:
            logger.info(
                "SKIP — cash reserve floor reached (balance=$%.2f, bet=$%.2f, floor=$%.2f)",
                cash_usd, bet_usd, reserve_floor,
            )
            return False
        return True
    except Exception as e:
        logger.warning("Cash reserve check failed: %s — allowing bet", e)
        return True


def _find_btc_direction_market(client: KalshiClient) -> dict | None:
    """Scan open Kalshi markets for a BTC daily direction market."""
    try:
        markets = client.get_markets(limit=100)
    except Exception as e:
        logger.warning("btc_direction: market fetch failed: %s", e)
        return None

    btc_keys    = ("bitcoin", "btc", "kxbtc")
    dir_keywords = ("higher", "above", "up", "increase", "close", "end of day")

    for market in markets:
        ticker = market.get("ticker", "").lower()
        title  = (market.get("_event_title") or market.get("title", "")).lower()
        if (
            any(k in ticker or k in title for k in btc_keys)
            and any(k in title for k in dir_keywords)
        ):
            return market

    return None


def _log_trade(entry: dict) -> None:
    try:
        with open(TRADES_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("trade log write failed: %s", e)


def run_btc_direction() -> dict | None:
    """
    Entry point called by APScheduler once daily at 8am ET.
    Returns the placed order dict, or None if skipped.
    """
    _reset_if_new_day()
    logger.info("── BTC direction strategy starting ──")

    # ── Fetch signals ──────────────────────────────────────────────────────────
    try:
        btc_price, change_24h = _fetch_btc_data()
        logger.info("BTC price: $%.2f  24h change: %+.2f%%", btc_price, change_24h)
    except Exception as e:
        logger.warning("btc_direction: CoinGecko fetch failed: %s", e)
        return None

    try:
        fear_greed, fg_label = _fetch_fear_greed()
        logger.info("Fear & Greed: %d (%s)", fear_greed, fg_label)
    except Exception as e:
        logger.warning("btc_direction: Fear & Greed fetch failed: %s", e)
        return None

    # ── Signal logic ───────────────────────────────────────────────────────────
    if fear_greed > GREED_THRESHOLD and change_24h > 0:
        side = "yes"
        reasoning = (
            f"Greed ({fear_greed}/100, '{fg_label}') + BTC {change_24h:+.2f}% "
            f"over 24h at ${btc_price:,.0f} — momentum aligned bullish. BUY YES."
        )
    elif fear_greed < FEAR_THRESHOLD and change_24h < 0:
        side = "no"
        reasoning = (
            f"Fear ({fear_greed}/100, '{fg_label}') + BTC {change_24h:+.2f}% "
            f"over 24h at ${btc_price:,.0f} — momentum aligned bearish. BUY NO."
        )
    else:
        reasoning = (
            f"Conflicting signals — Fear/Greed: {fear_greed} ({fg_label}), "
            f"24h change: {change_24h:+.2f}%. No trade."
        )
        logger.info("btc_direction SKIP — %s", reasoning)
        asyncio.run(tg.send_message(f"BTC direction: SKIP — {reasoning}"))
        return None

    # ── Find market ────────────────────────────────────────────────────────────
    try:
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv(
                "KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"
            ),
            paper_mode=PAPER_MODE,
        )
    except Exception as e:
        logger.warning("btc_direction: Kalshi client init failed: %s", e)
        return None

    market = _find_btc_direction_market(client)
    if market is None:
        logger.info("btc_direction: no BTC direction market found on Kalshi today — skipped")
        asyncio.run(tg.send_message(
            "BTC direction: no matching market on Kalshi today — skipped"
        ))
        return None

    ticker = market.get("ticker", "")
    title  = market.get("_event_title") or market.get("title", ticker)

    if ticker in _TRADED_TODAY:
        logger.info("btc_direction SKIP %s — already traded today", ticker)
        return None

    yes_price   = KalshiClient.yes_price_cents(market)
    price_cents = yes_price if side == "yes" else (100 - yes_price)
    contracts   = max(1, int(BET_USD))
    cost_usd    = round(contracts * price_cents / 100, 2)

    # Change 6: cash reserve check
    if not _cash_reserve_ok(client, cost_usd):
        logger.info("SKIP %s — protecting 25%% cash reserve", ticker)
        asyncio.run(tg.send_message("BTC direction: SKIP — cash reserve floor reached"))
        return None

    logger.info(
        "BTC DIRECTION %s — side=%s price=%d¢ contracts=%d | %s",
        ticker, side.upper(), price_cents, contracts, reasoning,
    )

    # ── Place order ────────────────────────────────────────────────────────────
    try:
        result = client.place_limit_order(
            ticker=ticker,
            side=side,
            price_cents=price_cents,
            contracts=contracts,
        )
    except Exception as e:
        logger.error("btc_direction order failed %s: %s", ticker, e)
        return None

    _TRADED_TODAY.add(ticker)

    entry = {
        "date":             datetime.now(timezone.utc).isoformat(),
        "strategy":         "btc_direction",
        "ticker":           ticker,
        "title":            title,
        "side":             side,
        "price_cents":      price_cents,
        "contracts":        contracts,
        "cost_usd":         cost_usd,
        "btc_price":        btc_price,
        "btc_24h_change":   round(change_24h, 4),
        "fear_greed":       fear_greed,
        "fear_greed_label": fg_label,
        "reasoning":        reasoning,
        "paper":            PAPER_MODE,
        "order_id":         result.get("order", {}).get("order_id", ""),
    }
    _log_trade(entry)

    direction = "UP" if side == "yes" else "DOWN"
    msg = (
        f"*BTC DIRECTION:* {direction} — ${cost_usd:.2f} on {side.upper()} "
        f"[{ticker}] at {price_cents}¢\n"
        f"F&G: {fear_greed} ({fg_label}) | BTC: ${btc_price:,.0f} ({change_24h:+.1f}% 24h)\n"
        f"_{reasoning}_"
    )
    logger.info(msg.replace("*", "").replace("_", ""))
    asyncio.run(tg.send_message(msg))

    logger.info("── BTC direction complete ──")
    return entry


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_btc_direction()
