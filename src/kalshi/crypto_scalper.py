"""
APEX Crypto Bracket Scalper — fast entries on live BTC/ETH price signals.

Runs every 3 minutes. Unlike the structural longshot fade (which holds to expiry),
this strategy enters AND exits within minutes based on live price movement:

  1. Fetch BTC/ETH spot price from CoinGecko (free, no API key)
  2. Find all Kalshi bracket markets closing within 0.5–4 hours
  3. If spot price is clearly above/below the bracket threshold (>3% buffer),
     the outcome is almost certain — buy YES before the market fully prices it
  4. The position_exit.py manager sells when we hit 10¢ profit or stop-loss

Why crypto brackets are scalp-friendly:
  - BTC/ETH prices are real-time knowable (CoinGecko)
  - A bracket priced at 80¢ when BTC is 5% above threshold will drift to 95¢
    in the 30-60 min before close — that's a 15¢ gain per contract
  - Volume is deep (100k+ contracts) so fills are fast
  - This is NOT the structural multi-bracket problem — we hold ONE bracket
    and exit before expiry, so cross-bracket correlation doesn't apply

Ticker format:
  KXBTC-26APR1417-B74000  → BTC above $74,000 at 2pm Apr 14 (B = above)
  KXBTC-26APR1417-T73750  → BTC at/below $73,750 at 2pm Apr 14 (T = at/below)
  KXETH-26APR1417-B2250   → ETH above $2,250 at 2pm Apr 14
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

_HERE = Path(__file__).parent
_APEX_DIR = Path("/opt/apex")
for _p in [str(_HERE), str(_APEX_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(_HERE / ".env")
load_dotenv(_APEX_DIR / ".env")

import telegram_notify as tg
from crypto_risk import crypto_compound_bet_usd
from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

PAPER_MODE      = os.getenv("APEX_ENV", "paper").lower() == "paper"
TRADES_LOG_PATH = Path(os.getenv("TRADES_LOG", "/opt/apex/trades.log"))

BASE_BET_USD    = float(os.getenv("CRYPTO_BASE_BET_USD", "5.0"))
CRYPTO_BET_MULTIPLIER = float(os.getenv("CRYPTO_BET_MULTIPLIER", "4.0"))
MAX_BET_USD     = float(os.getenv("CRYPTO_MAX_BET_USD", "30.0"))
MAX_POSITIONS   = 2      # max 1 BTC + 1 ETH at a time
BUFFER_PCT      = 0.05   # require 5% price buffer — ETH/BTC move 3-5% in hours
MIN_HOURS       = 0.5    # at least 30 min to close
MAX_HOURS       = 4.0    # no more than 4 hours out
MAX_ENTRY_CENTS = 93     # don't enter if already >93¢ (fully priced in, no room)

_COINGECKO_URL  = "https://api.coingecko.com/api/v3/simple/price"
_TRACKED_FILE   = Path("/opt/apex/crypto_scalp_positions.json")

# Ticker regex: KXBTC-26APR1417-B74000 → groups: asset, date, hour, direction, threshold
# hour is extracted to enforce one-position-per-asset-per-close-hour dedup
_CRYPTO_RE = re.compile(
    r"^KX(BTC|ETH)-(\d{2}[A-Z]{3}\d{2})(\d{2})-([BT])(\d+)$"
)


def _get_live_prices() -> dict[str, float]:
    """Fetch BTC and ETH spot prices from CoinGecko (no API key needed)."""
    try:
        resp = requests.get(
            _COINGECKO_URL,
            params={"ids": "bitcoin,ethereum", "vs_currencies": "usd"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "btc": float(data.get("bitcoin",  {}).get("usd", 0)),
            "eth": float(data.get("ethereum", {}).get("usd", 0)),
        }
    except Exception as e:
        logger.warning("CoinGecko price fetch failed: %s", e)
        return {}


def _parse_ticker(ticker: str) -> dict | None:
    """
    Parse a crypto bracket ticker.
    Returns {asset, close_key, direction, threshold} or None.
      B = "above" (YES wins if price > threshold)
      T = "below" (YES wins if price <= threshold)
    close_key = "{asset}_{date}{hour}" e.g. "eth_26APR1404"
    Used for one-position-per-asset-per-close-hour dedup.
    """
    m = _CRYPTO_RE.match(ticker)
    if not m:
        return None
    asset_raw, date_str, hour_str, direction_char, number_str = (
        m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    )
    return {
        "asset":     asset_raw.lower(),
        "close_key": f"{asset_raw.lower()}_{date_str}{hour_str}",
        "direction": "above" if direction_char == "B" else "below",
        "threshold": float(number_str),
    }


def _load_tracked() -> set[str]:
    """
    Load close_keys already entered today — format: "{asset}_{date}{hour}".
    One entry per asset per close-hour prevents correlated multi-bracket exposure
    (the bug that caused all 3 ETH positions to lose simultaneously on 2026-04-14).
    """
    try:
        if _TRACKED_FILE.exists():
            data = json.loads(_TRACKED_FILE.read_text())
            today = datetime.now(timezone.utc).date().isoformat()
            if data.get("date") == today:
                return set(data.get("close_keys", []))
    except Exception:
        pass
    return set()


def _save_tracked(close_keys: set[str]) -> None:
    try:
        _TRACKED_FILE.write_text(json.dumps({
            "date":       datetime.now(timezone.utc).date().isoformat(),
            "close_keys": list(close_keys),
        }))
    except Exception as e:
        logger.warning("Could not save crypto_scalp_positions: %s", e)


def _hours_left(close_str: str) -> float:
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


def _crypto_compound_bet_usd(entry_cents: int, buffer_pct: float) -> float:
    return crypto_compound_bet_usd(
        entry_cents=entry_cents,
        buffer_pct=buffer_pct,
        base_bet_usd=BASE_BET_USD,
        bet_multiplier=CRYPTO_BET_MULTIPLIER,
        max_bet_usd=MAX_BET_USD,
    )


def run_crypto_scalp() -> list[dict]:
    """
    Entry point called by APScheduler every 3 minutes.
    Returns list of positions opened this run.
    """
    logger.info("── Crypto scalp scan starting ──")

    prices = _get_live_prices()
    if not prices or not prices.get("btc"):
        logger.warning("Crypto scalp: no price data — skipping")
        return []

    btc = prices["btc"]
    eth = prices["eth"]
    logger.info("Spot prices — BTC=$%s  ETH=$%s", f"{btc:,.0f}", f"{eth:,.0f}")

    try:
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv(
                "KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
            paper_mode=PAPER_MODE,
        )
    except Exception as e:
        logger.warning("Crypto scalp: client init failed: %s", e)
        return []

    # Fetch open BTC + ETH bracket markets
    markets: list[dict] = []
    for series in ("KXBTC", "KXETH"):
        try:
            data = client._get("/markets", params={
                "limit": 40, "status": "open", "series_ticker": series,
            })
            markets.extend(data.get("markets", []))
        except Exception as e:
            logger.warning("Crypto scalp: %s fetch failed: %s", series, e)

    tracked = _load_tracked()
    if len(tracked) >= MAX_POSITIONS:
        logger.info("Crypto scalp: max positions (%d) already open — skipping", MAX_POSITIONS)
        return []

    orders = []

    for market in markets:
        ticker = market.get("ticker", "")

        parsed = _parse_ticker(ticker)
        if not parsed:
            continue

        asset     = parsed["asset"]
        close_key = parsed["close_key"]
        direction = parsed["direction"]
        threshold = parsed["threshold"]
        spot      = btc if asset == "btc" else eth

        # One position per asset per close-hour — prevents correlated multi-bracket loss
        if close_key in tracked:
            logger.debug("SKIP %s — already have %s position this close window", ticker, asset.upper())
            continue

        if spot == 0:
            continue

        # Require clear buffer from threshold
        if direction == "above":
            buffer = (spot - threshold) / threshold
        else:
            buffer = (threshold - spot) / threshold

        if buffer < BUFFER_PCT:
            continue

        close_str = (market.get("expected_expiration_time")
                     or market.get("close_time", ""))
        hours = _hours_left(close_str)
        if not (MIN_HOURS <= hours <= MAX_HOURS):
            continue

        # Buy YES — whether direction=above or below, if buffer > 0 the YES
        # outcome is currently clearly on track
        yes_cents   = KalshiClient.yes_price_cents(market)
        entry_cents = yes_cents

        if entry_cents > MAX_ENTRY_CENTS:
            logger.info(
                "SKIP %s — already fully priced at %d¢, no scalp room",
                ticker, entry_cents,
            )
            continue

        # Also skip if yes is priced below 50¢ for an "above" signal — something is wrong
        if direction == "above" and yes_cents < 50:
            logger.info(
                "SKIP %s — YES=%d¢ but spot is %.1f%% above threshold — market may be stale",
                ticker, yes_cents, buffer * 100,
            )
            continue

        bet_usd = _crypto_compound_bet_usd(entry_cents=entry_cents, buffer_pct=buffer)
        contracts = max(1, int(bet_usd / (entry_cents / 100)))
        cost_usd  = round(contracts * entry_cents / 100, 2)

        logger.info(
            "CRYPTO SCALP %s — %s $%,.0f %s $%,.0f (%.1f%% buffer) "
            "| BUY YES ×%d @%d¢ cost=$%.2f | %.1fh left",
            ticker, asset.upper(), spot,
            ">" if direction == "above" else "<",
            threshold, buffer * 100,
            contracts, entry_cents, cost_usd, hours,
        )

        try:
            result = client.place_limit_order(
                ticker=ticker,
                side="yes",
                price_cents=entry_cents,
                contracts=contracts,
            )
        except Exception as e:
            logger.error("Crypto scalp order failed %s: %s", ticker, e)
            continue

        tracked.add(close_key)
        entry = {
            "date":               datetime.now(timezone.utc).isoformat(),
            "strategy":           "crypto_scalp",
            "ticker":             ticker,
            "side":               "yes",
            "price_cents":        entry_cents,
            "contracts":          contracts,
            "cost_usd":           cost_usd,
            "asset":              asset,
            "direction":          direction,
            "threshold":          threshold,
            "spot_at_entry":      spot,
            "buffer_pct":         round(buffer * 100, 2),
            "hours_left":         round(hours, 2),
            "paper":              PAPER_MODE,
            "order_id":           result.get("order", {}).get("order_id", ""),
        }
        orders.append(entry)
        _log_trade(entry)

        direction_word = "above" if direction == "above" else "below"
        payout_usd = round(contracts * 1.00, 2)
        profit_usd = round(payout_usd - cost_usd, 2)
        msg = (
            f"Just placed a crypto bet!\n"
            f"{asset.upper()} is ${spot:,.0f} right now — clearly {direction_word} the ${threshold:,.0f} mark. "
            f"We bet ${cost_usd:.2f} that it stays that way. "
            f"If we're right in the next {hours:.1f}h → profit +${profit_usd:.2f}"
        )
        asyncio.run(tg.send_message(msg))

        if len(tracked) >= MAX_POSITIONS:
            break

    _save_tracked(tracked)
    logger.info(
        "── Crypto scalp complete | markets=%d new_positions=%d ──",
        len(markets), len(orders),
    )
    return orders


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_crypto_scalp()
