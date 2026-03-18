"""
APEX — Autonomous Prediction EXchange agent.
Scans Kalshi prediction markets every 15 minutes and places Kelly-sized bets.
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

# Load env from same directory as this file
load_dotenv(Path(__file__).parent / ".env")

import brain
import kelly as kelly_module
import market_intel
import negrisk_scanner
import sheets_logger
import telegram_notify as tg
from kalshi_client import KalshiClient

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "apex.log"),
    ],
)
# Enable DEBUG on kalshi_client so auth header details are visible
logging.getLogger("kalshi_client").setLevel(logging.DEBUG)
logger = logging.getLogger("apex_agent")

# ── Config ────────────────────────────────────────────────────────────────────
TRADES_LOG = Path(__file__).parent / "trades.log"
DAILY_CALLS_LOG = Path(__file__).parent / "daily_calls.json"
DAILY_CLAUDE_BUDGET = 50
PAPER_MODE = os.getenv("APEX_ENV", "paper").lower() == "paper"
BANKROLL = float(os.getenv("APEX_BANKROLL", "150.0"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.05"))
MAX_POSITIONS = 10
MIN_VOLUME = 100
MIN_HOURS_TO_CLOSE = 0.5   # 30 minutes
MAX_DAYS_TO_CLOSE = 14


def _get_client() -> KalshiClient:
    return KalshiClient(
        key_id=os.getenv("KALSHI_API_KEY_ID", ""),
        private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
        paper_mode=PAPER_MODE,
    )


def _log_trade(entry: dict) -> None:
    with open(TRADES_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _read_trades_today() -> list[dict]:
    if not TRADES_LOG.exists():
        return []
    today = datetime.now(timezone.utc).date().isoformat()
    trades = []
    for line in TRADES_LOG.read_text().splitlines():
        try:
            t = json.loads(line)
            if t.get("date", "").startswith(today):
                trades.append(t)
        except Exception:
            pass
    return trades


def _hours_until_close(close_time_str: str) -> float:
    """Return hours until market closes. Returns 0 on parse failure."""
    try:
        from datetime import datetime
        close = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (close - now).total_seconds() / 3600
    except Exception:
        return 0.0


PAUSE_FLAG = Path(__file__).parent.parent.parent / "paused.flag"  # local dev
_PAUSE_FLAG_SERVER = Path("/opt/apex/paused.flag")


def _is_paused() -> bool:
    return _PAUSE_FLAG_SERVER.exists() or PAUSE_FLAG.exists()


def _read_daily_budget() -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        if DAILY_CALLS_LOG.exists():
            data = json.loads(DAILY_CALLS_LOG.read_text())
            if data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "count": 0}


def _increment_daily_budget(data: dict) -> None:
    data["count"] = data.get("count", 0) + 1
    try:
        DAILY_CALLS_LOG.write_text(json.dumps(data))
    except Exception as e:
        logger.warning("Could not save daily_calls.json: %s", e)


# ── Schedule 1: Market scan every 15 minutes ─────────────────────────────────
def scan_markets() -> None:
    if _is_paused():
        logger.info("Trading paused — skipping scan.")
        return
    daily = _read_daily_budget()
    if daily["count"] >= DAILY_CLAUDE_BUDGET:
        logger.info("Daily Claude budget reached (%d/%d) — skipping scan.", daily["count"], DAILY_CLAUDE_BUDGET)
        return
    logger.info("── Market scan starting ──")
    try:
        client = _get_client()
    except ValueError as e:
        logger.warning("Kalshi client not ready: %s", e)
        return
    except Exception as e:
        logger.error("Failed to init Kalshi client: %s", e)
        asyncio.run(tg.send_error(f"Kalshi client init failed: {e}"))
        return

    # Check position count
    try:
        positions = client.get_positions()
        open_positions = [p for p in positions if p.get("total_traded", 0) > 0]
        if len(open_positions) >= MAX_POSITIONS:
            logger.info("Max positions (%d) reached, skipping scan.", MAX_POSITIONS)
            return
        positions_used = len(open_positions)
    except Exception as e:
        logger.warning("Could not fetch positions: %s", e)
        positions_used = 0

    # Fetch markets
    try:
        markets = client.get_markets(limit=20)
    except Exception as e:
        logger.error("Failed to fetch markets: %s", e)
        asyncio.run(tg.send_error(f"Market fetch failed: {e}"))
        return

    logger.info("Fetched %d markets", len(markets))

    for market in markets:
        if positions_used >= MAX_POSITIONS:
            break

        ticker = market.get("ticker", "")
        try:
            volume = float(market.get("volume_fp") or market.get("volume", 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0
        # Prefer expected_expiration_time (actual game/event end) over close_time (safety net)
        close_time = (market.get("expected_expiration_time")
                      or market.get("close_time", ""))
        hours_left = _hours_until_close(close_time)

        # Filter: minimum volume
        if volume < MIN_VOLUME:
            logger.info("SKIP %s — volume %d < %d", ticker, volume, MIN_VOLUME)
            continue

        # Filter: time window
        if hours_left < MIN_HOURS_TO_CLOSE:
            logger.info("SKIP %s — closes in %.1fh (too soon, min=%.1fh) close_time=%s",
                        ticker, hours_left, MIN_HOURS_TO_CLOSE, close_time)
            continue
        if hours_left > MAX_DAYS_TO_CLOSE * 24:
            logger.info("SKIP %s — closes in %.1fd (too far, max=%dd) close_time=%s",
                        ticker, hours_left / 24, MAX_DAYS_TO_CLOSE, close_time)
            continue

        logger.info("PASS %s — volume=%d hours_left=%.1f → sending to brain", ticker, volume, hours_left)

        # Small delay to avoid Anthropic rate limits when scanning many markets
        import time as _time
        _time.sleep(2)

        # Brain analysis
        try:
            decision = brain.analyze_market(market)
            _increment_daily_budget(daily)
        except Exception as e:
            logger.error("brain.analyze_market error for %s: %s", ticker, e)
            continue

        action = decision.get("action", "SKIP")
        if action == "SKIP":
            continue

        # Kelly sizing
        our_prob = float(decision.get("our_probability", 0.5))
        yes_price = KalshiClient.yes_price_cents(market)
        market_prob = yes_price / 100.0
        if action == "BUY_NO":
            market_prob = 1.0 - market_prob

        bet_usd = kelly_module.kelly_bet(
            bankroll=BANKROLL,
            our_probability=our_prob,
            market_probability=market_prob,
            kelly_fraction=KELLY_FRACTION,
            max_pct=MAX_POSITION_PCT,
        )

        if bet_usd <= 0:
            logger.info("Kelly returned 0 for %s, skipping.", ticker)
            continue

        side = "yes" if action == "BUY_YES" else "no"
        price_cents = yes_price if side == "yes" else (100 - yes_price)
        edge_pct = abs(decision.get("edge", 0)) * 100

        # Place order (or paper log)
        try:
            order_result = client.place_order(
                ticker=ticker,
                side=side,
                amount_cents=int(bet_usd * 100),
                price_cents=price_cents,
            )
            positions_used += 1
        except Exception as e:
            logger.error("place_order failed for %s: %s", ticker, e)
            asyncio.run(tg.send_error(f"Order failed {ticker}: {e}"))
            continue

        # Log trade
        trade_entry = {
            "date": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "title": market.get("title", ""),
            "action": action,
            "side": side,
            "bet_usd": bet_usd,
            "price_cents": price_cents,
            "edge": decision.get("edge"),
            "our_probability": our_prob,
            "market_probability": market_prob,
            "confidence": decision.get("confidence"),
            "reasoning": decision.get("reasoning", ""),
            "paper": PAPER_MODE,
            "order_id": order_result.get("order", {}).get("order_id", ""),
        }
        _log_trade(trade_entry)

        asyncio.run(tg.send_trade_alert(
            market_title=market.get("title", ticker),
            side=side.upper(),
            amount_usd=bet_usd,
            edge_pct=edge_pct,
            reasoning=decision.get("reasoning", ""),
        ))
        logger.info("Trade placed: %s %s $%.2f edge=%.1f%%", ticker, side, bet_usd, edge_pct)

    logger.info("── Market scan complete ──")


# ── Schedule 2: Daily summary at 9am ET ──────────────────────────────────────
def daily_summary() -> None:
    logger.info("── Daily summary ──")
    trades = _read_trades_today()
    if not trades:
        logger.info("No trades today.")
        return

    # Rough P&L estimate (would need settlement data for real P&L)
    total_bet = sum(t.get("bet_usd", 0) for t in trades)
    wins = sum(1 for t in trades if float(t.get("edge", 0)) > 0)
    win_rate = wins / len(trades) if trades else 0

    asyncio.run(tg.send_daily_summary(
        pnl=0.0,      # Real P&L requires checking settled positions
        trades=len(trades),
        win_rate=win_rate,
        bankroll=BANKROLL,
    ))

    sheets_logger.log_daily_summary(
        date=datetime.now(timezone.utc).date().isoformat(),
        trades=len(trades),
        wins=wins,
        losses=len(trades) - wins,
        pnl=0.0,
        bankroll=BANKROLL,
        win_rate=win_rate,
    )


# ── Startup ───────────────────────────────────────────────────────────────────
def startup() -> None:
    logger.info("APEX agent starting | mode=%s bankroll=$%.2f", "PAPER" if PAPER_MODE else "LIVE", BANKROLL)

    # Try to get real balance
    balance = BANKROLL
    try:
        client = _get_client()
        bal_data = client.get_balance()
        balance = bal_data.get("balance", BANKROLL) / 100  # Kalshi returns cents
    except Exception as e:
        logger.warning("Could not fetch Kalshi balance: %s", e)

    asyncio.run(tg.send_startup(
        balance=balance,
        mode="PAPER" if PAPER_MODE else "LIVE",
    ))


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Start inbound Telegram command handler in background thread
    tg.start_bot_listener()

    startup()

    scheduler = BlockingScheduler(timezone="America/New_York")

    # Scan every 15 minutes
    scheduler.add_job(
        scan_markets,
        trigger=IntervalTrigger(minutes=15),
        id="market_scan",
        name="Kalshi market scan",
        replace_existing=True,
        max_instances=1,
    )

    # Market intelligence every 30 minutes
    scheduler.add_job(
        market_intel.run_market_intel,
        trigger=IntervalTrigger(minutes=30),
        id="market_intel",
        name="Market intelligence scan",
        replace_existing=True,
        max_instances=1,
    )

    # NegRisk arb scanner every 5 minutes
    scheduler.add_job(
        negrisk_scanner.run_negrisk_scan,
        trigger=IntervalTrigger(minutes=5),
        id="negrisk_scan",
        name="Polymarket NegRisk arb scanner",
        replace_existing=True,
        max_instances=1,
    )

    # Daily summary at 9am ET
    scheduler.add_job(
        daily_summary,
        trigger=CronTrigger(hour=9, minute=0, timezone="America/New_York"),
        id="daily_summary",
        name="Daily P&L summary",
        replace_existing=True,
    )

    logger.info("Scheduler started. Scan every 15min. Intel every 30min. NegRisk every 5min. Daily summary at 09:00 ET.")

    # Run initial intel + market scan on startup
    market_intel.run_market_intel()
    negrisk_scanner.run_negrisk_scan()
    scan_markets()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("APEX agent stopped.")
