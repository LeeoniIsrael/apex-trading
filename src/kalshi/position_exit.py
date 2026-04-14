"""
APEX Position Exit Manager — takes profits early, cuts losers fast.

Runs every 5 minutes. For each open position tracked in trades.log:
  - If we're up >= 12% from entry: sell to lock profit
  - If we're down >= 15% from entry: cut the loss
  - If market closes in < 20 min: sell regardless (avoid last-minute illiquidity)

This converts the default "hold to expiry" approach into an active profit-taker,
freeing capital sooner and generating more frequent transactions.
"""
import asyncio
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

import telegram_notify as tg
from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

PAPER_MODE      = os.getenv("APEX_ENV", "paper").lower() == "paper"
TRADES_LOG_PATH = Path(os.getenv("TRADES_LOG", "/opt/apex/trades.log"))

PROFIT_TARGET   = 0.12   # 12% gain from entry price → sell
STOP_LOSS       = -0.15  # 15% loss from entry price → cut
NEAR_EXPIRY_H   = 0.33   # < 20 min left → always sell


def _load_open_trades() -> dict[str, dict]:
    """Read trades.log; return most-recent trade per ticker."""
    if not TRADES_LOG_PATH.exists():
        return {}
    by_ticker: dict[str, dict] = {}
    try:
        for line in TRADES_LOG_PATH.read_text().splitlines():
            try:
                t = json.loads(line)
                # Only entries from buy strategies (not exits themselves)
                if ("ticker" in t and "side" in t
                        and "price_cents" in t
                        and t.get("action", "buy") != "sell"):
                    by_ticker[t["ticker"]] = t
            except Exception:
                pass
    except Exception as e:
        logger.warning("Could not read trades.log: %s", e)
    return by_ticker


def _hours_left(close_str: str) -> float:
    try:
        close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        return (close - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        return 99.0


def _log_exit(entry: dict) -> None:
    try:
        with open(TRADES_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("exit log write failed: %s", e)


def run_position_exit() -> list[dict]:
    """
    Check all open Kalshi positions. Sell any that have hit the profit target,
    stop-loss, or are near expiry. Returns list of exits executed.
    """
    logger.info("── Position exit scan starting ──")
    try:
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv(
                "KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
            paper_mode=PAPER_MODE,
        )
    except Exception as e:
        logger.warning("Position exit: client init failed: %s", e)
        return []

    try:
        positions = client.get_positions()
    except Exception as e:
        logger.warning("Position exit: could not fetch positions: %s", e)
        return []

    open_trades = _load_open_trades()
    exits = []

    for pos in positions:
        ticker  = pos.get("ticker", "")
        pos_qty = int(pos.get("position", 0))
        if pos_qty == 0:
            continue

        side      = "yes" if pos_qty > 0 else "no"
        contracts = abs(pos_qty)

        trade = open_trades.get(ticker)
        if not trade:
            continue  # no trades.log record — can't compute entry price

        entry_cents = int(trade.get("price_cents", 50))

        # Current market price for our side
        try:
            market_data = client.get_market(ticker)
            market = market_data.get("market", market_data)
        except Exception:
            continue

        yes_cents     = KalshiClient.yes_price_cents(market)
        current_cents = yes_cents if side == "yes" else (100 - yes_cents)
        profit_pct    = (current_cents - entry_cents) / entry_cents

        close_str = (market.get("expected_expiration_time")
                     or market.get("close_time", ""))
        hours = _hours_left(close_str)

        should_exit = False
        reason      = ""

        if profit_pct >= PROFIT_TARGET:
            should_exit = True
            reason = f"profit target {profit_pct:.1%}"
        elif profit_pct <= STOP_LOSS:
            should_exit = True
            reason = f"stop-loss {profit_pct:.1%}"
        elif hours <= NEAR_EXPIRY_H:
            should_exit = True
            reason = f"near expiry ({hours*60:.0f} min left)"

        if not should_exit:
            logger.debug(
                "%s %s — entry=%d¢ now=%d¢ P&L=%.1f%% %.1fh left — holding",
                ticker, side, entry_cents, current_cents, profit_pct * 100, hours,
            )
            continue

        # Sell at current price - 1¢ to ensure fill
        sell_price = max(1, current_cents - 1)
        profit_usd = round(contracts * (sell_price - entry_cents) / 100, 2)

        logger.info(
            "EXIT %s %s ×%d — entry=%d¢ now=%d¢ P&L=%.1f%% → SELL @%d¢ (%s)",
            ticker, side, contracts, entry_cents, current_cents,
            profit_pct * 100, sell_price, reason,
        )

        try:
            result = client.sell_position(ticker, side, sell_price, contracts)
        except Exception as e:
            logger.error("sell_position failed %s: %s", ticker, e)
            continue

        exit_entry = {
            "date":             datetime.now(timezone.utc).isoformat(),
            "strategy":         "position_exit",
            "ticker":           ticker,
            "side":             side,
            "action":           "sell",
            "contracts":        contracts,
            "entry_price_cents": entry_cents,
            "exit_price_cents": sell_price,
            "profit_usd":       profit_usd,
            "reason":           reason,
            "paper":            PAPER_MODE,
            "order_id":         result.get("order", {}).get("order_id", ""),
        }
        exits.append(exit_entry)
        _log_exit(exit_entry)

        sign = "+" if profit_usd >= 0 else ""
        msg = (
            f"*PROFIT TAKEN:* {ticker} {side.upper()} — "
            f"entry {entry_cents}¢ → exit {sell_price}¢ "
            f"×{contracts} = ${sign}{profit_usd:.2f} ({profit_pct:.1%}) [{reason}]"
        )
        logger.info(msg.replace("*", ""))
        asyncio.run(tg.send_message(msg))

    logger.info("── Position exit scan complete — %d exits ──", len(exits))
    return exits


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_position_exit()
