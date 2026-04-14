"""
APEX Feedback Loop — learns from settled Kalshi markets.

Every hour:
  1. Read trades.log for all tickers we traded.
  2. For each unseen ticker, fetch the market from Kalshi API.
  3. If the market has a result ("yes" or "no"), calculate our P&L
     from our own trade data (side, price_cents, contracts).
  4. Append new entries to /opt/apex/learning_log.json.
  5. Expose get_edge_calibration() so brain.py can inject real
     historical win rates into each Claude prompt.

Why this approach:
  Kalshi's /portfolio/positions endpoint returns realized_pnl=0 for
  settled positions and clears the position quantity after settlement,
  making it impossible to determine side or profit. Instead, we own
  the trade data in trades.log and only need the market result from
  the API to compute accurate P&L.
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
TRADES_LOG_PATH   = Path(os.getenv("TRADES_LOG",   "/opt/apex/trades.log"))
PAPER_MODE        = os.getenv("APEX_ENV", "paper").lower() == "paper"

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


def _load_trades() -> list[dict]:
    """Read trades.log; return only entries that have the 'strategy' key."""
    if not TRADES_LOG_PATH.exists():
        return []
    trades = []
    try:
        for line in TRADES_LOG_PATH.read_text().splitlines():
            try:
                t = json.loads(line)
                if "strategy" in t and "ticker" in t:
                    trades.append(t)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Could not read trades.log: %s", e)
    return trades


def _fetch_market_result(client: KalshiClient, ticker: str) -> str | None:
    """
    Return the market result ("yes" or "no") if the market has settled,
    or None if it is still open or the result field is absent.
    """
    try:
        data = client._get(f"/markets/{ticker}")
        market = data.get("market", data)
        result = market.get("result", "")
        if result and result.lower() in ("yes", "no"):
            return result.lower()
    except Exception as e:
        logger.debug("Could not fetch market %s: %s", ticker, e)
    return None


def run_feedback_loop() -> int:
    """
    Cross-reference trades.log with Kalshi market results.
    For every settled market we traded, record the outcome and P&L.
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

    trades = _load_trades()
    if not trades:
        logger.info("Feedback loop: no trades found in trades.log.")
        return 0

    existing     = _load_log()
    seen_tickers = {e["ticker"] for e in existing}

    # Deduplicate trades by ticker — keep the last entry per ticker
    # (in case a market was traded more than once on different days)
    by_ticker: dict[str, dict] = {}
    for t in trades:
        by_ticker[t["ticker"]] = t

    new_entries: list[dict] = []
    checked = 0

    for ticker, trade in by_ticker.items():
        if ticker in seen_tickers:
            continue

        result = _fetch_market_result(client, ticker)
        if result is None:
            # Market still open or result unavailable — skip for now
            continue

        checked += 1
        side        = trade.get("side", "yes").lower()
        price_cents = int(trade.get("price_cents", 50))
        contracts   = int(trade.get("contracts", 1))
        title       = trade.get("title", "")
        category    = _infer_category(ticker, title)

        # Determine outcome: our side matches the resolved result
        won = (side == result)

        if won:
            profit_usd = round(contracts * (100 - price_cents) / 100, 2)
            outcome    = "won"
        else:
            profit_usd = round(-contracts * price_cents / 100, 2)
            outcome    = "lost"

        entry = {
            "ticker":               ticker,
            "side":                 side,
            "price_cents_at_entry": price_cents,
            "contracts":            contracts,
            "result":               result,
            "outcome":              outcome,
            "profit_usd":           profit_usd,
            "market_category":      category,
            "strategy":             trade.get("strategy", "unknown"),
            "date":                 trade.get("date", datetime.now(timezone.utc).isoformat()),
        }
        new_entries.append(entry)
        seen_tickers.add(ticker)
        logger.info(
            "LEARN %s | side=%s price=%d¢ ×%d | result=%s | %s | P&L=$%+.2f | cat=%s",
            ticker, side, price_cents, contracts, result, outcome, profit_usd, category,
        )

    if new_entries:
        _save_log(existing + new_entries)
        logger.info(
            "Feedback loop: checked=%d new=%d (total=%d)",
            checked, len(new_entries), len(existing) + len(new_entries),
        )
    else:
        logger.info("Feedback loop: 0 new settled markets found (checked %d).", checked)

    return len(new_entries)


def get_edge_calibration() -> str:
    """
    Read learning_log.json and return a plain-English calibration string
    for injection into the brain.py system prompt.

    Returns empty string if fewer than 5 settled bets exist (not enough signal).
    """
    entries = _load_log()
    if len(entries) < 5:
        return ""

    # Bucket by category + side
    buckets: dict[str, dict] = {}
    for e in entries:
        key = f"{e.get('market_category', 'other')} {e.get('side', '?')}-side"
        if key not in buckets:
            buckets[key] = {"bets": 0, "wins": 0, "pnl": 0.0}
        buckets[key]["bets"] += 1
        if e.get("outcome") == "won":
            buckets[key]["wins"] += 1
        buckets[key]["pnl"] += float(e.get("profit_usd", 0))

    total_pnl = sum(b["pnl"] for b in buckets.values())
    lines = [
        f"Historical performance (from {len(entries)} settled bets, total P&L=${total_pnl:+.2f}):"
    ]
    for key, stats in sorted(buckets.items()):
        bets    = stats["bets"]
        wins    = stats["wins"]
        win_pct = wins / bets * 100 if bets else 0
        pnl     = stats["pnl"]
        lines.append(
            f"- {key}: {bets} bets, {wins} wins ({win_pct:.0f}% win rate), P&L=${pnl:+.2f}"
        )

    return "\n".join(lines)
