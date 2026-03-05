"""APEX autonomous trading loop.

Schedule:
  - 09:31 ET: Morning signal scan + log decisions (no orders yet during build)
  - 15:45 ET: Afternoon rebalance check
  - 16:05 ET: End-of-day analysis + journal entry

Start:  April 1, 2026 at midnight ET
Stop:   May 1, 2026 at midnight ET
"""

from __future__ import annotations

import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.agent.brain import (
    end_of_day_analysis,
    evaluate_signal,
    get_market_regime,
    init_lgbm_filter,
)
from src.agent.executor import get_portfolio
from src.config import settings
from src.data.market import fetch_bars
from src.data.schema import init_db
from src.strategy.momentum import generate_signals

logger = logging.getLogger(__name__)

MARKET_TZ = "America/New_York"

EXPERIMENT_START = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
EXPERIMENT_END   = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)

LOOKBACK_DAYS = 90   # days of price history to fetch each scan
TRAIN_DAYS    = 730  # days of history to train LightGBM at startup (~2 years)
TOP_N         = 5    # momentum: long top-5

UNIVERSE = [
    # Tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    # Finance
    "JPM", "BAC", "GS", "BRK-B", "V",
    # Healthcare
    "JNJ", "UNH", "LLY", "ABBV",
    # Consumer
    "HD", "MCD", "NKE", "COST",
    # Energy
    "XOM", "CVX", "COP", "SLB",
]


# ─── Data helpers ────────────────────────────────────────────────────────────

def _bars_to_df_map(rows: list[dict]) -> dict[str, pd.DataFrame]:
    """Reshape flat bar rows [{symbol, timestamp, open, ...}] into {symbol: DataFrame}."""
    frames: dict[str, list[dict]] = {}
    for r in rows:
        frames.setdefault(r["symbol"], []).append(r)

    result = {}
    for sym, sym_rows in frames.items():
        df = pd.DataFrame(sym_rows).sort_values("timestamp")
        df = df.rename(columns={"open": "open", "high": "high", "low": "low",
                                 "close": "close", "volume": "volume"})
        df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
        result[sym] = df
    return result


def _fetch_universe_prices(
    symbols: list[str],
    lookback_days: int = LOOKBACK_DAYS,
) -> dict[str, pd.DataFrame]:
    """Fetch recent daily bars for all universe symbols from Alpaca."""
    now   = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=lookback_days)
    try:
        rows = fetch_bars(symbols, start=start, end=now)
        df_map = _bars_to_df_map(rows)
        logger.info("Fetched bars: %d symbols, lookback=%dd", len(df_map), lookback_days)
        return df_map
    except Exception as exc:
        logger.error("Failed to fetch universe prices: %s", exc)
        return {}


def _fetch_spy_close(lookback_days: int = LOOKBACK_DAYS) -> pd.Series:
    """Fetch SPY daily close for regime detection."""
    now   = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=lookback_days)
    try:
        rows = fetch_bars(["SPY"], start=start, end=now)
        df_map = _bars_to_df_map(rows)
        spy_df = df_map.get("SPY")
        if spy_df is None or spy_df.empty:
            logger.warning("No SPY data returned — regime defaults to trending")
            return pd.Series(dtype=float)
        return spy_df["close"]
    except Exception as exc:
        logger.error("Failed to fetch SPY close: %s", exc)
        return pd.Series(dtype=float)


# ─── DuckDB logging ──────────────────────────────────────────────────────────

def _log_signal(conn, *, timestamp: datetime, symbol: str, strategy: str,
                signal: str, confidence: float | None, features: dict) -> None:
    conn.execute(
        """
        INSERT INTO signals (id, timestamp, symbol, strategy, signal, confidence, features)
        VALUES (nextval('seq_signals'), ?, ?, ?, ?, ?, ?)
        """,
        [timestamp, symbol, strategy, signal, confidence, json.dumps(features)],
    )


def _log_agent_event(conn, *, timestamp: datetime, level: str,
                     message: str, metadata: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO agent_logs (id, timestamp, level, message, metadata)
        VALUES (nextval('seq_agent_logs'), ?, ?, ?, ?)
        """,
        [timestamp, level, message, json.dumps(metadata or {})],
    )


# ─── Scheduled jobs ──────────────────────────────────────────────────────────

def morning_scan() -> None:
    """Full pipeline: prices → momentum signals → regime → LightGBM gate → decisions → DuckDB.

    During build phase (before April 1): no orders are placed.
    Decisions are logged to DuckDB for review but executor is never called.
    """
    now = datetime.now(tz=timezone.utc)
    if now < EXPERIMENT_START or now >= EXPERIMENT_END:
        logger.info("Outside experiment window — skipping morning scan")
        return

    logger.info("=== APEX Morning Scan %s ===", now.date())
    conn = init_db()

    try:
        # ── 1. Fetch prices ─────────────────────────────────────────────────
        prices  = _fetch_universe_prices(UNIVERSE)
        spy_close = _fetch_spy_close(lookback_days=max(LOOKBACK_DAYS, 90))

        if not prices:
            logger.error("Morning scan aborted — no price data available")
            _log_agent_event(conn, timestamp=now, level="ERROR",
                             message="Morning scan aborted: no price data")
            return

        # ── 2. Market regime ────────────────────────────────────────────────
        regime = get_market_regime(spy_close) if not spy_close.empty else "trending"
        logger.info("Market regime: %s", regime.upper())

        # ── 3. Current positions ────────────────────────────────────────────
        portfolio        = get_portfolio()
        current_positions = {p["symbol"] for p in portfolio["positions"]}
        logger.info(
            "Portfolio: cash=$%.2f equity=$%.2f positions=%d",
            portfolio["cash"], portfolio["equity"], len(current_positions),
        )

        # ── 4. Momentum signals ─────────────────────────────────────────────
        signals = generate_signals(prices, current_positions, top_n=TOP_N)
        buys  = [s for s, sig in signals.items() if sig == "BUY"]
        sells = [s for s, sig in signals.items() if sig == "SELL"]
        logger.info("Momentum signals: %d BUY, %d SELL, %d HOLD",
                    len(buys), len(sells), len(signals) - len(buys) - len(sells))

        _log_agent_event(conn, timestamp=now, level="INFO",
                         message=f"Morning scan started — regime={regime}",
                         metadata={"regime": regime, "buys": buys, "sells": sells,
                                   "cash": portfolio["cash"], "equity": portfolio["equity"]})

        # ── 5. Evaluate each actionable signal ──────────────────────────────
        decisions_made = 0
        for symbol, raw_signal in signals.items():
            if raw_signal == "HOLD":
                continue

            sym_df = prices.get(symbol)
            market_context = {
                "regime":    regime,
                "timestamp": now.isoformat(),
                "raw_signal": raw_signal,
                "cash":      portfolio["cash"],
                "equity":    portfolio["equity"],
                "n_positions": len(current_positions),
            }

            decision = evaluate_signal(
                symbol=symbol,
                strategy_signal=raw_signal,
                market_context=market_context,
                portfolio_state=portfolio,
                df=sym_df,
                spy_close=spy_close if not spy_close.empty else None,
            )

            action     = decision.get("action", "HOLD")
            confidence = decision.get("confidence", 0.0)
            reasoning  = decision.get("reasoning", "")
            risk_factors = decision.get("risk_factors", [])

            logger.info(
                "DECISION: %-6s %-6s → %-4s (conf=%.2f) | %s",
                raw_signal, symbol, action, confidence, reasoning[:80],
            )

            # Log to signals table
            _log_signal(
                conn,
                timestamp=now,
                symbol=symbol,
                strategy="momentum",
                signal=action,
                confidence=confidence,
                features={
                    "raw_signal":   raw_signal,
                    "reasoning":    reasoning,
                    "risk_factors": risk_factors,
                    "regime":       regime,
                    "lgbm_proba":   market_context.get("lgbm_proba"),
                },
            )

            # NOTE: executor not called during build phase.
            # When agent goes live April 1, add:
            #   if action == "BUY":  submit_market_order(symbol, qty, "BUY", reasoning)
            #   if action == "SELL": submit_market_order(symbol, qty, "SELL", reasoning)

            decisions_made += 1

        _log_agent_event(conn, timestamp=now, level="DECISION",
                         message=f"Morning scan complete — {decisions_made} decisions logged",
                         metadata={"decisions": decisions_made, "regime": regime})

        logger.info("Morning scan complete — %d decisions logged to DuckDB", decisions_made)

    except Exception as exc:
        logger.exception("Morning scan failed: %s", exc)
        try:
            _log_agent_event(conn, timestamp=now, level="ERROR",
                             message=f"Morning scan exception: {exc}")
        except Exception:
            pass
    finally:
        conn.close()


def afternoon_rebalance() -> None:
    now = datetime.now(tz=timezone.utc)
    if now < EXPERIMENT_START or now >= EXPERIMENT_END:
        return
    logger.info("=== APEX Afternoon Rebalance %s ===", now.date())

    conn = init_db()
    try:
        prices           = _fetch_universe_prices(UNIVERSE, lookback_days=30)
        portfolio        = get_portfolio()
        current_positions = {p["symbol"] for p in portfolio["positions"]}

        if not prices or not current_positions:
            return

        signals = generate_signals(prices, current_positions, top_n=TOP_N)
        sells   = [s for s, sig in signals.items() if sig == "SELL"]
        if sells:
            logger.info("Afternoon rebalance: %d SELL candidates: %s", len(sells), sells)
            _log_agent_event(conn, timestamp=now, level="INFO",
                             message=f"Afternoon rebalance: {len(sells)} SELL candidates",
                             metadata={"sells": sells})
        else:
            logger.info("Afternoon rebalance: no adjustments needed")
    except Exception as exc:
        logger.exception("Afternoon rebalance failed: %s", exc)
    finally:
        conn.close()


def end_of_day() -> None:
    now = datetime.now(tz=timezone.utc)
    if now < EXPERIMENT_START or now >= EXPERIMENT_END:
        return
    logger.info("=== APEX End-of-Day %s ===", now.date())

    conn = init_db()
    try:
        portfolio = get_portfolio()
        today_str = now.date().isoformat()

        # Pull today's decisions from agent_logs
        rows = conn.execute(
            "SELECT message, metadata FROM agent_logs WHERE timestamp::date = ? ORDER BY timestamp",
            [today_str],
        ).fetchall()
        trades_today = [{"message": r[0], "metadata": r[1]} for r in rows]

        market_summary = {"date": today_str, "note": "Build phase — no live trades"}
        analysis = end_of_day_analysis(trades_today, portfolio, market_summary)

        logger.info("End-of-day analysis:\n%s", analysis)
        _log_agent_event(conn, timestamp=now, level="INFO",
                         message="End-of-day analysis complete",
                         metadata={"analysis": analysis[:500]})
    except Exception as exc:
        logger.exception("End-of-day analysis failed: %s", exc)
    finally:
        conn.close()


# ─── Startup: train LightGBM ─────────────────────────────────────────────────

def _startup_train_lgbm() -> None:
    """Train the LightGBM filter on 2 years of history at agent startup."""
    logger.info("Startup: training LightGBM filter on %d days of history…", TRAIN_DAYS)
    now   = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=TRAIN_DAYS)
    try:
        rows    = fetch_bars(UNIVERSE, start=start, end=now)
        df_map  = _bars_to_df_map(rows)
        if df_map:
            init_lgbm_filter(df_map)
        else:
            logger.warning("Startup: no price data for LightGBM training — gate will be open")
    except Exception as exc:
        logger.error("Startup LightGBM training failed: %s — gate will be open", exc)


# ─── Scheduler ───────────────────────────────────────────────────────────────

def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=MARKET_TZ)

    scheduler.add_job(
        morning_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=31, timezone=MARKET_TZ),
        id="morning_scan",
        name="Morning signal scan",
    )
    scheduler.add_job(
        afternoon_rebalance,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=MARKET_TZ),
        id="afternoon_rebalance",
        name="Afternoon rebalance",
    )
    scheduler.add_job(
        end_of_day,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone=MARKET_TZ),
        id="end_of_day",
        name="End-of-day analysis",
    )
    return scheduler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    _startup_train_lgbm()

    scheduler = build_scheduler()

    def _shutdown(signum, frame):
        logger.info("Shutting down APEX agent…")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(
        "APEX agent ready. Experiment window: %s → %s",
        EXPERIMENT_START.date(), EXPERIMENT_END.date(),
    )
    scheduler.start()


if __name__ == "__main__":
    main()
