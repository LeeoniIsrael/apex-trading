"""
APEX Feedback Loop — learns from settled Kalshi positions.

Every hour:
  1. Fetch settled positions from Kalshi API.
  2. Append any new ones to /opt/apex/learning_log.json.
  3. Expose get_edge_calibration() so brain.py can inject real
     historical win rates into each Claude prompt.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).parent
_APEX_DIR = Path("/opt/apex")
for _p in [str(_HERE), str(_APEX_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(_HERE / ".env")
load_dotenv(_APEX_DIR / ".env")

from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

LEARNING_LOG_PATH = Path(os.getenv("LEARNING_LOG", "/opt/apex/learning_log.json"))
PAPER_MODE = os.getenv("APEX_ENV", "paper").lower() == "paper"

_CRYPTO_KW  = ("btc", "bitcoin", "eth", "ethereum", "crypto", "kxbtc", "kxeth",
               "solana", "sol", "doge", "xrp")
_WEATHER_KW = ("kxhigh", "kxtemp", "weather", "temperature", "temp")
_SPORTS_KW  = ("nba", "nfl", "nhl", "mlb", "soccer", "kxnba", "kxnfl", "kxnhl",
               "kxmlb", "game", "match", "kxnbagame", "kxnflgame")


def _infer_category(ticker: str, title: str = "") -> str:
    s = (ticker + " " + title).lower()
    if any(k in s for k in _CRYPTO_KW):
        return "crypto"
    if any(k in s for k in _WEATHER_KW):
        return "weather"
    if any(k in s for k in _SPORTS_KW):
        return "sports"
    return "other"


def _load_log() -> list[dict]:
    if not LEARNING_LOG_PATH.exists():
        return []
    try:
        return json.loads(LEARNING_LOG_PATH.read_text())
    except Exception:
        return []


def _save_log(entries: list[dict]) -> None:
    try:
        LEARNING_LOG_PATH.write_text(json.dumps(entries, indent=2))
    except Exception as e:
        logger.warning("Could not write learning_log.json: %s", e)


def run_feedback_loop() -> int:
    """
    Fetch settled positions from Kalshi, append new ones to learning_log.json.
    Returns number of new entries added.
    """
    logger.info("── Feedback loop starting ──")
    try:
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv(
                "KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
            paper_mode=PAPER_MODE,
        )
    except Exception as e:
        logger.warning("Feedback loop: Kalshi client init failed: %s", e)
        return 0

    # Fetch settled positions — try settlement_status param first, fall back
    try:
        data = client._get("/portfolio/positions",
                           params={"settlement_status": "settled", "limit": 200})
        positions = data.get("market_positions", [])
        # If no results with filter, retry without and filter locally
        if not positions:
            data = client._get("/portfolio/positions", params={"limit": 200})
            positions = [
                p for p in data.get("market_positions", [])
                if str(p.get("settlement_status", "")).lower() == "settled"
            ]
    except Exception as e:
        logger.warning("Feedback loop: positions fetch failed: %s", e)
        return 0

    if not positions:
        logger.info("Feedback loop: no settled positions found.")
        return 0

    existing = _load_log()
    seen_tickers = {e["ticker"] for e in existing}

    new_entries = []
    for pos in positions:
        ticker = pos.get("ticker", "")
        if not ticker or ticker in seen_tickers:
            continue

        # Determine side: the position field is net contracts;
        # positive = YES held, negative = NO held
        position_qty = int(pos.get("position", 0) or 0)
        side = "yes" if position_qty >= 0 else "no"

        # Entry price: use average trade price if available
        avg_price = pos.get("average_trade_price") or pos.get("last_price_dollars")
        try:
            price_cents = round(float(avg_price) * 100) if avg_price is not None else 50
        except (ValueError, TypeError):
            price_cents = 50

        # P&L: Kalshi returns realized_pnl in cents
        raw_pnl = pos.get("realized_pnl") or pos.get("pnl") or 0
        try:
            profit_usd = round(float(raw_pnl) / 100, 2)
        except (ValueError, TypeError):
            profit_usd = 0.0

        outcome = "won" if profit_usd > 0 else "lost"

        title = pos.get("market_title") or pos.get("title") or ""
        category = _infer_category(ticker, title)

        settled_date = (pos.get("settlement_time") or pos.get("close_time")
                        or datetime.now(timezone.utc).isoformat())

        entry = {
            "ticker":               ticker,
            "side":                 side,
            "price_cents_at_entry": price_cents,
            "outcome":              outcome,
            "profit_usd":           profit_usd,
            "market_category":      category,
            "date":                 settled_date,
        }
        new_entries.append(entry)
        seen_tickers.add(ticker)
        logger.info(
            "LEARN %s | %s %s | %s | profit=$%.2f | cat=%s",
            ticker, side, price_cents, outcome, profit_usd, category,
        )

    if new_entries:
        _save_log(existing + new_entries)
        logger.info("Feedback loop: added %d new entries (total=%d)",
                    len(new_entries), len(existing) + len(new_entries))
    else:
        logger.info("Feedback loop: 0 new entries (all already logged).")

    return len(new_entries)


def get_edge_calibration() -> str:
    """
    Read learning_log.json and return a plain-English calibration string
    suitable for injection into the brain.py system prompt.

    Returns empty string if fewer than 5 settled bets exist (not enough signal).
    """
    entries = _load_log()
    if len(entries) < 5:
        return ""

    # Bucket by category+side
    buckets: dict[str, dict] = {}
    for e in entries:
        key = f"{e.get('market_category', 'other')} {e.get('side', '?')}-side"
        if key not in buckets:
            buckets[key] = {"bets": 0, "wins": 0}
        buckets[key]["bets"] += 1
        if e.get("outcome") == "won":
            buckets[key]["wins"] += 1

    lines = [f"Historical performance (from {len(entries)} settled bets):"]
    for key, stats in sorted(buckets.items()):
        bets = stats["bets"]
        wins = stats["wins"]
        win_pct = wins / bets * 100 if bets else 0
        lines.append(f"- {key}: {bets} bets, {wins} wins, {win_pct:.0f}% win rate")

    return "\n".join(lines)
