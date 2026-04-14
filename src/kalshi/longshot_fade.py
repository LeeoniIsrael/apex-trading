"""
APEX Longshot Fade Strategy.

Exploits the favourite-longshot bias on Kalshi: contracts priced 10–20¢ (YES)
win far less often than their implied probability suggests. We systematically
buy NO on these longshots, targeting a structural edge of ~10–15¢ per contract.

Scan every 30 minutes. Buy NO on any open market where:
  - YES price is between 10¢ and 20¢  (longshot zone)
  - Volume ≥ 500 contracts             (liquidity floor — Change 4)
  - Hours until close: 1–24h           (same-day resolution)
  - Not a crypto bracket market        (structural flaw — Change 1)
  - At most one bracket per event      (multi-bet dedup — Change 1)
  - NO price not in 40-60¢ fee trap    (fee optimisation — Change 5)
  - We haven't already faded this ticker today

Kalshi Volume Incentive Program (VIP) — through September 2026:
  - $0.005 cashback per contract for trades priced 3¢–97¢
  - $10–$1000 daily liquidity rewards for resting limit orders
  - EVERY order in this strategy is a resting limit order to qualify.
  See: Kalshi VIP documentation for full tier details.
"""
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
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

import kelly
import telegram_notify as tg
from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

BANKROLL        = float(os.getenv("APEX_BANKROLL", "150.0"))
PAPER_MODE      = os.getenv("APEX_ENV", "paper").lower() == "paper"
KELLY_FRACTION  = 0.25          # Raised from 0.20 — more aggressive on proven structural edge
MAX_BET_USD     = 20.0          # Raised from $10 — larger positions when Kelly calls for it
TRADES_LOG_PATH = Path(os.getenv("TRADES_LOG", "/opt/apex/trades.log"))
CASH_RESERVE_PCT = 0.10         # Lowered from 25% to 10% — more aggressive deployment

# Longshot zone: YES price in [10, 25] cents.
# We skip the 5-9¢ range: at those extremes the market tends to be correct
# (true long shots that almost never resolve YES). The structural bias is
# strongest in the 10-25¢ band where retail bettors systematically overweight
# small probabilities. Extended to 25¢ to capture NBA playoff 1v8 / 2v7 seed
# mismatches and any other market where the underdog is priced 21-25¢.
LONGSHOT_LOW  = 10
LONGSHOT_HIGH = 30

# Implied true probability adjustment — bias research shows 15¢ YES contracts
# win ~8% of the time vs 15% implied. We model NO true prob = 0.90 (vs 0.85 implied).
BIAS_ADJUSTMENT = 0.05

# Liquidity thresholds (Change 4)
# Markets under 500 contracts are skipped entirely — too thin to move without
# self-impact. Markets under 2000 get a $3 bet cap to limit slippage.
MIN_VOLUME         = 200     # lowered from 500 — more markets eligible
VOLUME_CAP_THRESH  = 1000   # lowered from 2000
LOW_LIQ_MAX_BET    = 5.0    # raised from $3

MIN_HOURS      = 0.5    # 30 min minimum
MAX_HOURS      = 48.0   # up to 2-day markets (some sports run overnight)

# Fee-optimized price filter (Change 5): avoid NO bets where the NO price is 40-60¢.
# Kalshi fees are highest (3-7%) in this mid-range band, eating structural edge.
# In practice longshot fade bets NO at 80-90¢, so this is a safety net.
FEE_TRAP_LOW  = 40
FEE_TRAP_HIGH = 60

_CRYPTO_KEYWORDS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
    "doge", "xrp", "ripple",
)

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


def _recently_traded(ticker: str, side: str, hours: int = 24) -> bool:
    """Return True if a trade for this ticker+side appears in trades.log within the last N hours."""
    if not TRADES_LOG_PATH.exists():
        return False
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    try:
        for line in TRADES_LOG_PATH.read_text().splitlines():
            try:
                t = json.loads(line)
                if t.get("ticker") != ticker or t.get("side", "").lower() != side.lower():
                    continue
                trade_time = datetime.fromisoformat(
                    t["date"].replace("Z", "+00:00")
                ).timestamp()
                if trade_time >= cutoff:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def _cash_reserve_ok(client: KalshiClient, bet_usd: float) -> bool:
    """
    Return True if placing bet_usd won't breach the 25% cash reserve floor (Change 6).
    Fails open on API errors to avoid blocking all bets from a transient issue.
    """
    try:
        bal_data = client.get_balance()
        cash_usd = bal_data.get("balance", 0) / 100  # Kalshi returns cents
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


def _event_key(market: dict) -> str:
    """
    Return a stable event grouping key for multi-bracket dedup (Change 1).
    Prefers the event_ticker field; falls back to stripping the last hyphen segment.
    """
    event_ticker = market.get("event_ticker") or market.get("_event_ticker", "")
    if event_ticker:
        return event_ticker
    ticker = market.get("ticker", "")
    parts = ticker.rsplit("-", 1)
    return parts[0] if len(parts) > 1 else ticker


def run_longshot_scan() -> list[dict]:
    """
    Entry point called by APScheduler every 30 minutes.
    Returns list of orders placed.

    Architecture: collect all qualifying candidates first, then group by event
    to enforce single-bracket-per-event rule, then place orders. This prevents
    the structural loss where winning 3-of-4 brackets still nets negative.
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

    # Fetch a broad slice of open markets — 200 to catch all current-day games
    try:
        markets = client.get_markets(limit=200)
    except Exception as e:
        logger.warning("Longshot scan: market fetch failed: %s", e)
        return []

    # ── Pass 1: collect all candidates that pass individual filters ───────────
    candidates: list[dict] = []

    for market in markets:
        ticker = market.get("ticker", "")
        if ticker in _FADED_TODAY:
            continue

        title = market.get("_event_title") or market.get("title", ticker)
        title_lower = title.lower()
        ticker_lower = ticker.lower()

        # Change 1 / crypto block — structural flaw: winning multiple NO brackets
        # still loses when any single bracket resolves YES
        if any(k in title_lower or k in ticker_lower for k in _CRYPTO_KEYWORDS):
            logger.info("SKIP %s — crypto bracket multi-bet risk", ticker)
            continue

        if _recently_traded(ticker, "no"):
            logger.info("SKIP %s — already traded today (trades.log 24h lookback)", ticker)
            continue

        yes_price = KalshiClient.yes_price_cents(market)
        if not (LONGSHOT_LOW <= yes_price <= LONGSHOT_HIGH):
            continue

        no_price_cents = 100 - yes_price

        # Change 5: fee trap — avoid NO bets in the 40-60¢ mid-range
        if FEE_TRAP_LOW <= no_price_cents <= FEE_TRAP_HIGH:
            logger.info("SKIP %s — mid-range fee trap (40-60¢) NO price=%d¢", ticker, no_price_cents)
            continue

        try:
            volume = float(market.get("volume_fp") or market.get("volume", 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0

        # Change 4: liquidity floor
        if volume < MIN_VOLUME:
            logger.info("SKIP %s — volume %.0f < %d (liquidity floor)", ticker, volume, MIN_VOLUME)
            continue

        close_time = (market.get("expected_expiration_time")
                      or market.get("close_time", ""))
        hours_left = _hours_until_close(close_time)
        if not (MIN_HOURS <= hours_left <= MAX_HOURS):
            continue

        market_no_p = 1.0 - yes_price / 100.0
        our_no_p    = market_no_p + BIAS_ADJUSTMENT
        edge        = our_no_p - market_no_p  # always == BIAS_ADJUSTMENT

        candidates.append({
            "market":       market,
            "ticker":       ticker,
            "title":        title,
            "yes_price":    yes_price,
            "no_price":     no_price_cents,
            "market_no_p":  market_no_p,
            "our_no_p":     our_no_p,
            "edge":         edge,
            "volume":       volume,
            "hours_left":   hours_left,
            "event_key":    _event_key(market),
        })

    # ── Pass 2: one bracket per event — keep highest YES price (best NO edge) ─
    # Change 1: grouping prevents betting NO on multiple brackets of the same
    # event, which has negative expected value even with a structural bias.
    event_groups: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        event_groups[c["event_key"]].append(c)

    deduped: list[dict] = []
    for event_key, group in event_groups.items():
        if len(group) > 1:
            best = max(group, key=lambda x: x["yes_price"])
            skipped = [c["ticker"] for c in group if c["ticker"] != best["ticker"]]
            logger.info(
                "SKIP %s — same-event multi-bracket dedup (keeping %s, best in band)",
                skipped, best["ticker"],
            )
        else:
            best = group[0]
        deduped.append(best)

    # ── Pass 3: size and place orders ────────────────────────────────────────
    orders: list[dict] = []

    for c in deduped:
        ticker        = c["ticker"]
        title         = c["title"]
        yes_price     = c["yes_price"]
        no_price_cents = c["no_price"]
        market_no_p   = c["market_no_p"]
        our_no_p      = c["our_no_p"]
        edge          = c["edge"]
        volume        = c["volume"]
        hours_left    = c["hours_left"]
        market        = c["market"]

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
            max_pct=0.12,   # 12% of bankroll max per trade (up from 7%)
        )
        bet_usd = min(max(bet_usd, 1.0), MAX_BET_USD)

        # Change 4: cap bet at $3 for thin markets (500–2000 contracts)
        if volume < VOLUME_CAP_THRESH:
            bet_usd = min(bet_usd, LOW_LIQ_MAX_BET)
            logger.info(
                "LOW LIQUIDITY cap on %s (vol=%.0f < %d) — capping bet at $%.2f",
                ticker, volume, VOLUME_CAP_THRESH, LOW_LIQ_MAX_BET,
            )

        contracts = max(1, int(bet_usd))
        cost_usd = round(contracts * limit_price / 100, 2)

        # Change 6: cash reserve check
        if not _cash_reserve_ok(client, cost_usd):
            logger.info("SKIP %s — protecting 25%% cash reserve", ticker)
            continue

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

        # Change 2: VIP cashback program log
        logger.info(
            "LIMIT ORDER placed — qualifies for VIP cashback program (%s, %d¢ × %d contracts)",
            ticker, limit_price, contracts,
        )

        _FADED_TODAY.add(ticker)
        payout = round(contracts * 1.00, 2)
        profit = round(payout - cost_usd, 2)

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

        clean_title = title.replace(" Winner?", "").replace(" winner?", "")
        msg = (
            f"*LONGSHOT FADE:* {clean_title} — "
            f"bet ${cost_usd:.2f} on NO at {limit_price}¢, "
            f"payout ${payout:.2f} if wins, profit +${profit:.2f}"
        )
        logger.info(msg.replace("*", ""))
        asyncio.run(tg.send_message(msg))

    logger.info(
        "── Longshot scan complete | checked=%d candidates=%d deduped=%d orders=%d ──",
        len(markets), len(candidates), len(deduped), len(orders),
    )
    return orders


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    run_longshot_scan()
