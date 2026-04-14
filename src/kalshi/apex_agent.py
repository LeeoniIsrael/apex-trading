"""
APEX — Autonomous Prediction EXchange agent.
Scans Kalshi prediction markets every 15 minutes and places Kelly-sized bets.

Kalshi Volume Incentive Program (VIP) — through September 2026 (Change 2):
  - $0.005 cashback per contract for all trades priced between 3¢ and 97¢
  - $10–$1000 daily liquidity rewards for resting limit orders (maker orders)
  - Strategy: ALL orders are placed as resting limit orders (not market orders)
    to qualify for the daily liquidity reward tier.
  - Qualifying price range: 3¢–97¢ per contract (virtually all our markets)
  Reference: Kalshi VIP documentation for full tier/cashback schedule.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

# Load env from same directory as this file
load_dotenv(Path(__file__).parent / ".env")

import brain
import btc_direction
import feedback_loop
import kelly as kelly_module
import longshot_fade
import market_intel
import sheets_logger
import telegram_notify as tg
import weather_strategy
from kalshi_client import KalshiClient

# negrisk_scanner lives only on the server — guard the import gracefully so
# local dev and CI don't crash. Self-identified fix: missing module was causing
# silent AttributeError if the server file was ever absent.
try:
    import negrisk_scanner
    _HAS_NEGRISK = True
except ImportError:
    _HAS_NEGRISK = False
    logging.getLogger("apex_agent").warning(
        "negrisk_scanner not found — NegRisk arb scan disabled"
    )

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
TRADES_LOG        = Path(__file__).parent / "trades.log"
DAILY_CALLS_LOG   = Path(__file__).parent / "daily_calls.json"
DAILY_SNAPSHOTS   = Path("/opt/apex/daily_snapshots.json")  # Change 8
DAILY_CLAUDE_BUDGET  = 30
PAPER_MODE           = os.getenv("APEX_ENV", "paper").lower() == "paper"
BANKROLL             = float(os.getenv("APEX_BANKROLL", "150.0"))
KELLY_FRACTION       = float(os.getenv("KELLY_FRACTION", "0.35"))
MAX_POSITION_PCT     = float(os.getenv("MAX_POSITION_PCT", "0.05"))
MAX_POSITIONS        = 10
MIN_VOLUME           = 100
MIN_HOURS_TO_CLOSE   = 1.0   # at least 1 hour before close
MAX_DAYS_TO_CLOSE    = 1     # same-day resolution only (within 24 hours)

# Change 6: Cash reserve — never let cash drop below 25% of bankroll
CASH_RESERVE_PCT     = 0.25

# Change 7: Daily loss circuit breaker — if total deployed today exceeds this,
# pause all new bets for the rest of the day and alert via Telegram.
# Raised from $20 to $30 to allow more flexibility with NBA playoffs and elections.
DAILY_LOSS_LIMIT_USD = 30.0

# Liquidity thresholds (Change 4) — mirrors longshot_fade.py
MIN_VOLUME_HARD   = 500    # skip entirely below this
VOL_CAP_THRESH    = 2000   # cap bet at $3 below this
LOW_LIQ_MAX_BET   = 3.0

# Fee trap filter (Change 5) — avoid 43-57¢ mid-range contracts (narrowed from 40-60)
FEE_TRAP_LOW  = 43
FEE_TRAP_HIGH = 57


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


def _get_today_deployed_usd() -> float:
    """
    Sum of all cost_usd / bet_usd placed today from trades.log.
    Used by the daily circuit breaker (Change 7).
    """
    trades_today = _read_trades_today()
    return sum(float(t.get("cost_usd") or t.get("bet_usd") or 0) for t in trades_today)


def _hours_until_close(close_time_str: str) -> float:
    """Return hours until market closes. Returns 0 on parse failure."""
    try:
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


def _check_cash_reserve(client: KalshiClient, bet_usd: float) -> bool:
    """
    Return True if placing bet_usd won't breach the 25% cash reserve (Change 6).
    Fails open on API errors.
    """
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


# ── Schedule 1: Market scan every 15 minutes ─────────────────────────────────
def scan_markets() -> None:
    if _is_paused():
        logger.info("Trading paused — skipping scan.")
        return

    # Change 7: circuit breaker — pause if we've deployed >= $20 today
    deployed_today = _get_today_deployed_usd()
    if deployed_today >= DAILY_LOSS_LIMIT_USD:
        logger.info(
            "CIRCUIT BREAKER: $%.2f deployed today >= $%.2f limit — pausing bets until midnight",
            deployed_today, DAILY_LOSS_LIMIT_USD,
        )
        # Only send Telegram alert once per day (check last_circuit_break timestamp)
        _maybe_send_circuit_breaker_alert(deployed_today)
        return

    daily = _read_daily_budget()
    if daily["count"] >= DAILY_CLAUDE_BUDGET:
        logger.info("Daily Claude budget reached (%d/%d) — skipping scan.", daily["count"], DAILY_CLAUDE_BUDGET)
        return

    logger.info("── Market scan starting ──")
    tg.update_status("last_scan", datetime.now(timezone.utc).isoformat())

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

    # Fetch markets — 50 gives brain.py a wider view of the market landscape
    try:
        markets = client.get_markets(limit=50)
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

        # Prefer expected_expiration_time (actual game/event end) over close_time
        close_time = (market.get("expected_expiration_time")
                      or market.get("close_time", ""))
        hours_left = _hours_until_close(close_time)

        # Change 4: hard liquidity floor
        if volume < MIN_VOLUME_HARD:
            logger.info("SKIP %s — volume %d < %d (liquidity floor)", ticker, volume, MIN_VOLUME_HARD)
            continue

        # Filter: minimum volume (legacy brain threshold kept for context)
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

        # Change 5: fee trap filter — brain.py handles this too, but filter early to save API calls
        yes_price = KalshiClient.yes_price_cents(market)
        if FEE_TRAP_LOW <= yes_price <= FEE_TRAP_HIGH:
            logger.info("SKIP %s — mid-range fee trap (40-60¢) price=%d¢", ticker, yes_price)
            continue

        category = market.get("_event_category") or market.get("category", "unknown")
        logger.info("PASS %s — category=%s volume=%d hours_left=%.1fh → sending to brain",
                    ticker, category, volume, hours_left)

        # Small delay to avoid Anthropic rate limits when scanning many markets
        time.sleep(2)

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
        bet_usd = max(bet_usd, 2.00)  # minimum bet floor

        # Change 4: cap at $3 for low-liquidity markets
        if volume < VOL_CAP_THRESH:
            bet_usd = min(bet_usd, LOW_LIQ_MAX_BET)
            logger.info(
                "LOW LIQUIDITY cap on %s (vol=%.0f < %d) — capping bet at $%.2f",
                ticker, volume, VOL_CAP_THRESH, LOW_LIQ_MAX_BET,
            )

        # Change 6: cash reserve check
        if not _check_cash_reserve(client, bet_usd):
            logger.info("SKIP — cash reserve floor reached, protecting 25%% reserve")
            continue

        side = "yes" if action == "BUY_YES" else "no"
        price_cents = yes_price if side == "yes" else (100 - yes_price)
        edge_pct = abs(decision.get("edge", 0)) * 100

        # Place limit order (Change 2: always limit orders to qualify for VIP)
        try:
            order_result = client.place_order(
                ticker=ticker,
                side=side,
                amount_cents=int(bet_usd * 100),
                price_cents=price_cents,
            )
            # Change 2: VIP cashback log
            logger.info(
                "LIMIT ORDER placed — qualifies for VIP cashback program (%s, %d¢)",
                ticker, price_cents,
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
            "cost_usd": bet_usd,   # explicit cost_usd for circuit breaker reads
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


# Circuit breaker alerting — only fire Telegram once per triggered day
_circuit_breaker_alerted_date: str = ""

def _maybe_send_circuit_breaker_alert(deployed: float) -> None:
    global _circuit_breaker_alerted_date
    today = datetime.now(timezone.utc).date().isoformat()
    if _circuit_breaker_alerted_date == today:
        return
    _circuit_breaker_alerted_date = today
    try:
        client = _get_client()
        bal_data = client.get_balance()
        balance = bal_data.get("balance", BANKROLL * 100) / 100
    except Exception:
        balance = BANKROLL
    asyncio.run(tg.send_message(
        f"Daily loss limit hit — ${deployed:.2f} deployed today. "
        f"Pausing bets until midnight. Balance: ${balance:.2f}."
    ))


# ── Schedule 2: Morning briefing at 9am ET ───────────────────────────────────

def _load_snapshots() -> dict:
    """Load daily portfolio snapshots from /opt/apex/daily_snapshots.json."""
    try:
        if DAILY_SNAPSHOTS.exists():
            return json.loads(DAILY_SNAPSHOTS.read_text())
    except Exception:
        pass
    return {}


def _save_snapshot(date_iso: str, value: float) -> None:
    """Persist today's portfolio value; keep 30 days of history."""
    snapshots = _load_snapshots()
    snapshots[date_iso] = round(value, 2)
    # Trim to 30 most-recent days
    if len(snapshots) > 30:
        oldest = sorted(snapshots.keys())[0]
        del snapshots[oldest]
    try:
        DAILY_SNAPSHOTS.write_text(json.dumps(snapshots, indent=2))
    except Exception as e:
        logger.warning("Could not write daily_snapshots.json: %s", e)


def morning_briefing() -> None:
    """
    Change 8: Snapshot-based morning briefing.
    Fetches current portfolio value from Kalshi API, compares to yesterday's
    snapshot stored in daily_snapshots.json, then saves today's value.
    This gives accurate daily P&L independent of individual trade settlement.
    """
    logger.info("── Morning briefing ──")

    today     = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    # Fetch current portfolio value + open positions
    portfolio_value = BANKROLL
    n_positions = 0
    try:
        client = _get_client()
        bal_data = client.get_balance()
        portfolio_value = bal_data.get("balance", BANKROLL * 100) / 100
        positions = client.get_positions()
        n_positions = len([p for p in positions if p.get("total_traded", 0) > 0])
    except Exception as e:
        logger.warning("Could not fetch portfolio data for morning briefing: %s", e)

    # Compare to yesterday's snapshot
    snapshots = _load_snapshots()
    yesterday_val = snapshots.get(yesterday)

    if yesterday_val is not None:
        change = portfolio_value - yesterday_val
        sign = "+" if change >= 0 else ""
        change_str = f"yesterday: ${yesterday_val:.2f}, change: {sign}${change:.2f}"
    else:
        change_str = "first day on record"

    # Persist today's snapshot
    _save_snapshot(today, portfolio_value)

    msg = (
        f"Good morning. Portfolio: ${portfolio_value:.2f} ({change_str}). "
        f"Active positions: {n_positions}. Bot running."
    )
    logger.info(msg)
    asyncio.run(tg.send_message(msg))

    # Sheets logger
    day_number = (datetime.now(timezone.utc).date() - datetime(2026, 3, 3, tzinfo=timezone.utc).date()).days + 1
    change_pnl = (portfolio_value - (yesterday_val or portfolio_value))
    try:
        from datetime import date
        sheets_logger.log_daily_summary(
            str(date.today()), day_number,
            0, 0, 0,          # trade counts not tracked here; use feedback_loop
            round(change_pnl, 2), round(portfolio_value, 2),
            0.0,
        )
    except Exception as e:
        logger.warning("Sheets logger error: %s", e)


# ── Startup ───────────────────────────────────────────────────────────────────
def startup() -> None:
    logger.info("APEX agent starting | mode=%s bankroll=$%.2f", "PAPER" if PAPER_MODE else "LIVE", BANKROLL)

    balance = BANKROLL
    try:
        client = _get_client()
        bal_data = client.get_balance()
        balance = bal_data.get("balance", BANKROLL) / 100
    except Exception as e:
        logger.warning("Could not fetch Kalshi balance: %s", e)

    tg.update_status("active_strategies", ["brain", "longshot", "weather", "btc_direction"])
    tg.update_status("mode", "PAPER" if PAPER_MODE else "LIVE")
    asyncio.run(tg.send_startup(
        balance=balance,
        mode="PAPER" if PAPER_MODE else "LIVE",
    ))


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
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

    # NegRisk arb scanner every 5 minutes (only if module available)
    if _HAS_NEGRISK:
        scheduler.add_job(
            negrisk_scanner.run_negrisk_scan,
            trigger=IntervalTrigger(minutes=5),
            id="negrisk_scan",
            name="Polymarket NegRisk arb scanner",
            replace_existing=True,
            max_instances=1,
        )

    # Weather strategy every 6 hours (aligned with GFS model updates: 00Z/06Z/12Z/18Z)
    scheduler.add_job(
        weather_strategy.run_weather_scan,
        trigger=CronTrigger(hour="0,6,12,18", minute=5, timezone="UTC"),
        id="weather_scan",
        name="GFS ensemble weather strategy",
        replace_existing=True,
        max_instances=1,
    )

    # Longshot fade every 30 minutes
    scheduler.add_job(
        longshot_fade.run_longshot_scan,
        trigger=IntervalTrigger(minutes=30),
        id="longshot_fade",
        name="Longshot fade (structural bias)",
        replace_existing=True,
        max_instances=1,
    )

    # Feedback loop every hour — learn from settled positions
    scheduler.add_job(
        feedback_loop.run_feedback_loop,
        trigger=IntervalTrigger(minutes=60),
        id="feedback_loop",
        name="Settled position feedback loop",
        replace_existing=True,
        max_instances=1,
    )

    # BTC direction strategy at 8am ET
    scheduler.add_job(
        btc_direction.run_btc_direction,
        trigger=CronTrigger(hour=8, minute=0, timezone="America/New_York"),
        id="btc_direction",
        name="Bitcoin daily direction strategy",
        replace_existing=True,
        max_instances=1,
    )

    # Morning briefing at 9am ET
    scheduler.add_job(
        morning_briefing,
        trigger=CronTrigger(hour=9, minute=0, timezone="America/New_York"),
        id="morning_briefing",
        name="Daily morning briefing",
        replace_existing=True,
    )

    negrisk_str = "NegRisk 5min | " if _HAS_NEGRISK else ""
    logger.info(
        "Scheduler started. "
        "Brain scan 15min | Intel 30min | %s"
        "Weather 6h | Longshot 30min | Feedback 60min | "
        "BTC direction 08:00 ET | Daily 09:00 ET",
        negrisk_str,
    )

    # Run initial scans on startup
    feedback_loop.run_feedback_loop()
    market_intel.run_market_intel()
    if _HAS_NEGRISK:
        negrisk_scanner.run_negrisk_scan()
    weather_strategy.run_weather_scan()
    longshot_fade.run_longshot_scan()
    scan_markets()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("APEX agent stopped.")
