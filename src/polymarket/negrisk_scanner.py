"""
APEX Polymarket NegRisk Arbitrage Scanner.

In a multi-outcome market (e.g. 'Who wins the 2026 election?'), exactly one
outcome resolves YES.  Therefore the YES prices must sum to 1.00.  When they
sum to LESS than 1.00, buying YES on every outcome guarantees a risk-free
profit equal to (1 - sum).

The scanner:
  1. Fetches all active Polymarket events via the Gamma API.
  2. For each event with 2+ outcomes, sums all YES token prices.
  3. Flags any event where sum < 0.95 (5% buffer for fees/slippage).
  4. Logs opportunities to arb_opportunities.json.
  5. Sends a Telegram alert for any opportunity with profit > 2%.
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
# Allow running standalone or imported from apex_agent (which runs from /opt/apex)
_HERE = Path(__file__).parent
_KALSHI_DIR = _HERE.parent / "kalshi"
if str(_KALSHI_DIR) not in sys.path:
    sys.path.insert(0, str(_KALSHI_DIR))
# Also support flat /opt/apex layout where all .py files live together
_APEX_DIR = Path("/opt/apex")
if _APEX_DIR.exists() and str(_APEX_DIR) not in sys.path:
    sys.path.insert(0, str(_APEX_DIR))

from dotenv import load_dotenv

load_dotenv(_HERE / ".env")
load_dotenv(_KALSHI_DIR / ".env")

import telegram_notify as tg
from polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
ARB_LOG = Path(os.getenv("ARB_LOG_PATH", "/opt/apex/arb_opportunities.json"))
BANKROLL = float(os.getenv("APEX_BANKROLL", "150.0"))
ARB_THRESHOLD = 0.95      # sum of YES prices must be below this
ALERT_THRESHOLD = 0.02    # send Telegram if profit_pct > 2%
MAX_SPEND_PER_ARB = 50.0  # never risk more than $50 on one arb


def _parse_price(raw) -> float | None:
    """Extract a float YES price from the various Polymarket field shapes."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    # outcomePrices is sometimes a stringified list like "['0.52', '0.48']"
    if isinstance(raw, str):
        import ast
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list) and parsed:
                return float(parsed[0])
        except Exception:
            pass
    return None


def _extract_outcomes(event: dict) -> list[dict]:
    """
    Return a list of {title, yes_price} dicts for every outcome in a NegRisk event.

    Polymarket NegRisk structure: the event has a `markets` array where each
    element is a binary YES/NO market for one outcome.  Each market has:
      - outcomePrices: ["<yes_price>", "<no_price>"]   (stringified list)
      - lastTradePrice: <yes_price float>
      - question: the outcome label

    We want only the YES price per sub-market, then sum across all sub-markets.
    Only events where at least one market has negRisk=true are considered.
    """
    import ast

    markets = event.get("markets") or []
    if len(markets) < 2:
        return []

    # Only process NegRisk-flagged events
    if not any(m.get("negRisk") for m in markets):
        return []

    outcomes = []
    for m in markets:
        title = (m.get("groupItemTitle") or m.get("question") or
                 m.get("title") or "")

        # outcomePrices is ["yes_price", "no_price"] — take index 0
        op = m.get("outcomePrices")
        yes_price: float | None = None
        if op is not None:
            try:
                if isinstance(op, str):
                    op = ast.literal_eval(op)
                if isinstance(op, (list, tuple)) and len(op) >= 1:
                    yes_price = float(op[0])
            except Exception:
                pass

        # Fall back to lastTradePrice
        if yes_price is None:
            ltp = m.get("lastTradePrice")
            if ltp is not None:
                try:
                    yes_price = float(ltp)
                except (TypeError, ValueError):
                    pass

        if yes_price is not None and 0 < yes_price < 1:
            outcomes.append({"title": title, "price": yes_price})

    return outcomes


def _scan_event(event: dict) -> dict | None:
    """
    Return an arb opportunity dict if this event's YES prices sum < ARB_THRESHOLD,
    else None.
    """
    event_title = (event.get("title") or event.get("question") or
                   event.get("slug") or "Unknown")

    outcomes = _extract_outcomes(event)
    if len(outcomes) < 2:
        return None

    price_sum = sum(o["price"] for o in outcomes)

    if price_sum >= ARB_THRESHOLD or price_sum <= 0:
        return None

    profit_pct = (1.0 - price_sum) / price_sum * 100
    spend = min(MAX_SPEND_PER_ARB, BANKROLL * 0.1)

    # Distribute spend proportionally (inverse of price → buy cheap outcomes more)
    total_inv = sum(1.0 / o["price"] for o in outcomes if o["price"] > 0)
    allocations = []
    for o in outcomes:
        if o["price"] <= 0:
            continue
        weight = (1.0 / o["price"]) / total_inv if total_inv else 1.0 / len(outcomes)
        allocations.append({
            "outcome": o["title"],
            "price": round(o["price"], 4),
            "spend_usd": round(spend * weight, 2),
        })

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_id": event.get("id", ""),
        "event_title": event_title,
        "price_sum": round(price_sum, 4),
        "profit_pct": round(profit_pct, 2),
        "recommended_spend_usd": round(spend, 2),
        "num_outcomes": len(outcomes),
        "allocations": allocations,
    }


def _log_opportunity(opp: dict) -> None:
    """Append opportunity to ARB_LOG (JSON-lines style inside a JSON array)."""
    try:
        ARB_LOG.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if ARB_LOG.exists():
            try:
                existing = json.loads(ARB_LOG.read_text())
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        existing.append(opp)
        # Keep last 500 entries
        ARB_LOG.write_text(json.dumps(existing[-500:], indent=2))
    except Exception as e:
        logger.error("Failed to write arb_opportunities.json: %s", e)


def run_negrisk_scan() -> list[dict]:
    """
    Main entry point — called by APScheduler every 5 minutes.
    Returns list of opportunities found (may be empty).
    """
    logger.info("── NegRisk arb scan starting ──")
    client = PolymarketClient()
    events = client.get_events(limit=100)
    logger.info("Fetched %d events from Polymarket", len(events))

    opportunities: list[dict] = []
    for event in events:
        opp = _scan_event(event)
        if opp is None:
            continue
        opportunities.append(opp)
        _log_opportunity(opp)
        logger.info(
            "ARB | %s | sum=%.4f profit=%.2f%% spend=$%.2f across %d outcomes",
            opp["event_title"][:60],
            opp["price_sum"],
            opp["profit_pct"],
            opp["recommended_spend_usd"],
            opp["num_outcomes"],
        )
        if opp["profit_pct"] >= ALERT_THRESHOLD * 100:
            msg = (
                f"*ARB FOUND* — {opp['event_title']}\n"
                f"Sum: {opp['price_sum']:.4f} — Edge: {opp['profit_pct']:.2f}% "
                f"— Spend ${opp['recommended_spend_usd']:.2f} across "
                f"{opp['num_outcomes']} outcomes"
            )
            asyncio.run(tg.send_message(msg))

    logger.info(
        "── NegRisk scan complete | events=%d arbs=%d ──",
        len(events), len(opportunities),
    )
    return opportunities


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    results = run_negrisk_scan()
    if results:
        print(f"\n=== {len(results)} ARB OPPORTUNITIES ===")
        for r in results:
            print(
                f"  {r['event_title'][:70]:<70}  "
                f"sum={r['price_sum']:.4f}  profit={r['profit_pct']:.2f}%"
            )
    else:
        print("No arb opportunities found.")
