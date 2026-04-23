"""
APEX Parlay Engine v2 -- Research-backed 2-leg combo bets.

Leg priority (highest first):
  1. NBA PLAYER STATS -- most predictable. Star players routinely hit
     moderate thresholds (Curry 2+ threes, Green 6+ assists, etc).
     Claude Haiku researches each candidate before we commit.
  2. NBA game winners -- heavy favorites (72-88c YES)
  3. Crypto brackets -- live BTC/ETH price signal, 5%+ buffer

Stat series scanned: KXNBAPTS, KXNBAREB, KXNBAAST, KXNBA3PT, KXNBAPRA

Bet sizing (user-specified larger legs):
  - Floor: $10 per leg (min meaningful payout)
  - Max:   $25 per leg
  - Kelly fraction: 0.25, max_pct: 0.15

Fires at 5pm ET daily.
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

import kelly
import telegram_notify as tg
from kalshi_client import KalshiClient

logger = logging.getLogger(__name__)

BANKROLL           = float(os.getenv("APEX_BANKROLL", "150.0"))
PAPER_MODE         = os.getenv("APEX_ENV", "paper").lower() == "paper"
KELLY_FRACTION     = 0.25
BET_FLOOR_USD      = 10.0       # min per leg -- bigger payouts
MAX_BET_USD        = 25.0       # max per leg
LEGS_TARGET        = 2
TRADES_LOG_PATH    = Path(os.getenv("TRADES_LOG", "/opt/apex/trades.log"))
DRAWDOWN_PAUSE_PCT = 0.20

# Player stat sweet spot: likely but still has real payout
STAT_MIN_YES   = 70     # at least 70% likely
STAT_MAX_YES   = 91     # avoid fully priced (>91c = almost no profit)
STAT_MIN_VOL   = 150    # need some liquidity
STAT_MAX_HOURS = 48.0   # upcoming games only
STAT_MIN_HOURS = 1.0   # must be 1h+ out so game hasnt started
STAT_BIAS      = 0.03   # market slightly underestimates frequent hitters

# NBA game winner range -- clear favorites only
NBA_MIN_YES    = 72
NBA_MAX_YES    = 88
NBA_MIN_VOL    = 30
NBA_MAX_HOURS  = 72.0
NBA_MIN_HOURS  = 1.0
NBA_BIAS       = 0.04

# Crypto bracket -- high-confidence direction play
CRYPTO_MIN_YES    = 75
CRYPTO_MAX_YES    = 92
CRYPTO_BUFFER_PCT = 0.05
CRYPTO_MAX_HOURS  = 6.0
CRYPTO_MIN_HOURS  = 0.25  # crypto ok to enter closer
CRYPTO_BIAS       = 0.05

_COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
_CRYPTO_RE = re.compile(r"^KX(BTC|ETH)-(\d{2}[A-Z]{3}\d{2})(\d{2})-([BT])(\d+)$")

# Max Claude calls per parlay run (protects API credits)
MAX_RESEARCH_CALLS = 4
_RESEARCH_USED = 0


def _hours_until_close(close_str):
    try:
        close = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        return (close - datetime.now(timezone.utc)).total_seconds() / 3600
    except Exception:
        return 0.0


def _recently_traded(ticker, hours=22):
    if not TRADES_LOG_PATH.exists():
        return False
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    try:
        for line in TRADES_LOG_PATH.read_text().splitlines():
            try:
                t = json.loads(line)
                if t.get("ticker") != ticker:
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


def _cash_reserve_ok(client, total_cost):
    try:
        bal_data = client.get_balance()
        cash_usd = bal_data.get("balance", 0) / 100
        drawdown_floor = BANKROLL * (1 - DRAWDOWN_PAUSE_PCT)
        if cash_usd < drawdown_floor:
            logger.warning("DRAWDOWN PAUSE -- balance=$%.2f < floor=$%.2f", cash_usd, drawdown_floor)
            return False
        if (cash_usd - total_cost) < BANKROLL * 0.10:
            logger.info("SKIP -- cash reserve floor reached")
            return False
        return True
    except Exception as e:
        logger.warning("Cash reserve check failed: %s -- allowing bet", e)
        return True


def _log_trade(entry):
    try:
        with open(TRADES_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("trade log write failed: %s", e)


def _ev_score(our_p, market_p):
    if market_p <= 0:
        return 0.0
    edge = our_p - market_p
    payout_per_dollar = (1.0 - market_p) / market_p
    return edge * payout_per_dollar


def _size_bet(our_p, market_p):
    raw = kelly.kelly_bet(
        bankroll=BANKROLL,
        our_probability=our_p,
        market_probability=market_p,
        kelly_fraction=KELLY_FRACTION,
        max_pct=0.15,
    )
    return min(max(raw, BET_FLOOR_USD), MAX_BET_USD)


# ── Research tool -- Claude Haiku looks up real player stats ──────────────────

def _research_player_stat(title, player_name):
    """
    Ask Claude Haiku to search for the player's recent stats and confirm
    whether the market threshold is likely to be hit.
    Returns (our_probability, reasoning) or None if credits exhausted.
    """
    global _RESEARCH_USED
    if _RESEARCH_USED >= MAX_RESEARCH_CALLS:
        return None

    try:
        import anthropic
        ai = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=180,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
            system=(
                "You are a concise NBA stats analyst. Search for the player's recent "
                "game stats and respond ONLY in this format:\n"
                "PROB: XX%\nREASON: one sentence max."
            ),
            messages=[{
                "role": "user",
                "content": (
                    "Kalshi market: '" + title + "'\n"
                    "Search for " + player_name + " NBA stats last 5 games 2026. "
                    "What is the real probability (0-100%) this threshold is hit tonight? "
                    "Reply: PROB: XX% REASON: one sentence."
                ),
            }],
        )
        _RESEARCH_USED += 1
        # Parse response -- look through all content blocks for text
        text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text
        m = re.search(r"PROB:\s*(\d+)%", text)
        m2 = re.search(r"REASON:\s*(.+)", text)
        prob = float(m.group(1)) / 100.0 if m else None
        reason = m2.group(1)[:120].strip() if m2 else "Based on recent stats"
        logger.info("Research [%d/%d] %s -> prob=%.0f%% | %s",
                    _RESEARCH_USED, MAX_RESEARCH_CALLS, player_name,
                    (prob or 0) * 100, reason)
        return (prob, reason) if prob is not None else None
    except Exception as e:
        logger.warning("Research call failed for %s: %s", player_name, e)
        return None


def _extract_player_name(title):
    """
    'Draymond Green: 10+ assists' -> 'Draymond Green'
    'Kawhi Leonard: 35+ points'   -> 'Kawhi Leonard'
    """
    m = re.match(r"^([A-Za-z\s\.\'-]+?):", title)
    return m.group(1).strip() if m else title.split(":")[0].strip()


# ── Player stat scanner ───────────────────────────────────────────────────────

def _get_stat_legs(client):
    """Scan all stat series, research top candidates, return sorted legs."""
    stat_series = [
        "KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT",
        "KXNBAPLAYOFFPTS", "KXNBAPRA",
    ]

    raw_candidates = []
    for series in stat_series:
        try:
            d = client._get("/markets", params={
                "limit": 30, "status": "open", "series_ticker": series,
            })
            raw_candidates.extend(d.get("markets", []))
        except Exception as e:
            logger.warning("Parlay: %s fetch failed: %s", series, e)

    logger.info("Parlay: scanning %d total player stat markets", len(raw_candidates))

    candidates = []
    for market in raw_candidates:
        ticker = market.get("ticker", "")
        if _recently_traded(ticker):
            continue

        yes_price = KalshiClient.yes_price_cents(market)
        if not (STAT_MIN_YES <= yes_price <= STAT_MAX_YES):
            continue

        try:
            volume = float(market.get("volume_fp") or market.get("volume", 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0
        if volume < STAT_MIN_VOL:
            continue

        close_time = (market.get("expected_expiration_time")
                      or market.get("close_time", ""))
        hours_left = _hours_until_close(close_time)
        if not (STAT_MIN_HOURS <= hours_left <= STAT_MAX_HOURS):
            continue

        market_p = yes_price / 100.0
        our_p    = market_p + STAT_BIAS
        score    = _ev_score(our_p, market_p)

        title = market.get("title", ticker)
        player_name = _extract_player_name(title)
        # game_key: 'KXNBAPTS-26APR15GSWLAC-...' -> '26APR15GSWLAC'
        parts = ticker.split("-")
        game_key_stat = parts[1] if len(parts) >= 2 else ticker
        candidates.append({
            "source":     "player_stat",
            "ticker":     ticker,
            "title":      title,
            "yes_price":  yes_price,
            "market_p":   market_p,
            "our_p":      our_p,
            "score":      score,
            "hours_left": hours_left,
            "researched": False,
            "reasoning":  "",
            "player_name": player_name,
            "game_key":    game_key_stat,
        })

    # Sort by EV score, take top 6 to research
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:6]

    # Research each with Claude -- updates our_p and reasoning
    verified = []
    for c in top:
        player = _extract_player_name(c["title"])
        result = _research_player_stat(c["title"], player)
        if result is not None:
            prob, reason = result
            c["our_p"]    = prob
            c["score"]    = _ev_score(prob, c["market_p"])
            c["reasoning"] = reason
            c["researched"] = True
            # Only keep if research confirms positive edge
            if prob > c["market_p"]:
                verified.append(c)
        else:
            # No research available -- keep with raw estimate if score > 0
            if c["score"] > 0:
                verified.append(c)

    verified.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Parlay: %d verified player stat legs (from %d candidates)", len(verified), len(top))
    return verified


# ── NBA game winner scanner ───────────────────────────────────────────────────

def _game_key(ticker):
    parts = ticker.rsplit("-", 1)
    return parts[0] if len(parts) == 2 else ticker


def _get_nba_legs(client):
    try:
        data = client._get("/markets", params={
            "limit": 60, "status": "open", "series_ticker": "KXNBAGAME",
        })
        markets = data.get("markets", [])
    except Exception as e:
        logger.warning("Parlay: NBA game fetch failed: %s", e)
        return []

    candidates = []
    for market in markets:
        ticker = market.get("ticker", "")
        if _recently_traded(ticker):
            continue
        yes_price = KalshiClient.yes_price_cents(market)
        if not (NBA_MIN_YES <= yes_price <= NBA_MAX_YES):
            continue
        try:
            volume = float(market.get("volume_fp") or market.get("volume", 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0
        if volume < NBA_MIN_VOL:
            continue
        close_time = (market.get("expected_expiration_time") or market.get("close_time", ""))
        hours_left = _hours_until_close(close_time)
        if not (NBA_MIN_HOURS <= hours_left <= NBA_MAX_HOURS):
            continue
        market_p = yes_price / 100.0
        our_p    = market_p + NBA_BIAS
        title = (market.get("_event_title") or market.get("title", ticker))
        title = title.replace(" Winner?", "").replace(" winner?", "")
        candidates.append({
            "source": "nba_game", "ticker": ticker, "title": title,
            "yes_price": yes_price, "market_p": market_p, "our_p": our_p,
            "score": _ev_score(our_p, market_p), "hours_left": hours_left,
            "reasoning": "NBA heavy favorite", "game_key": _game_key(ticker),
        })

    # Dedup by game (keep favorite side)
    groups = {}
    for c in candidates:
        groups.setdefault(c["game_key"], []).append(c)
    deduped = [max(g, key=lambda x: x["yes_price"]) for g in groups.values()]
    deduped.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Parlay: %d qualifying NBA game legs", len(deduped))
    return deduped


# ── Crypto leg scanner ────────────────────────────────────────────────────────

def _get_crypto_legs(client):
    try:
        resp = requests.get(_COINGECKO_URL,
            params={"ids": "bitcoin,ethereum", "vs_currencies": "usd"}, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        prices = {
            "btc": float(data.get("bitcoin", {}).get("usd", 0)),
            "eth": float(data.get("ethereum", {}).get("usd", 0)),
        }
    except Exception as e:
        logger.warning("Parlay: CoinGecko failed: %s", e)
        return []

    markets = []
    for series in ("KXBTC", "KXETH"):
        try:
            d = client._get("/markets", params={
                "limit": 40, "status": "open", "series_ticker": series,
            })
            markets.extend(d.get("markets", []))
        except Exception:
            pass

    candidates = []
    seen_keys = set()
    for market in markets:
        ticker = market.get("ticker", "")
        if _recently_traded(ticker):
            continue
        m = _CRYPTO_RE.match(ticker)
        if not m:
            continue
        asset_raw = m.group(1)
        close_key = asset_raw.lower() + "_" + m.group(2) + m.group(3)
        direction = "above" if m.group(4) == "B" else "below"
        threshold = float(m.group(5))
        spot = prices["btc"] if asset_raw == "BTC" else prices["eth"]
        if close_key in seen_keys or spot == 0:
            continue
        buf = ((spot - threshold) / threshold if direction == "above"
               else (threshold - spot) / threshold)
        if buf < CRYPTO_BUFFER_PCT:
            continue
        close_time = (market.get("expected_expiration_time") or market.get("close_time", ""))
        hours_left = _hours_until_close(close_time)
        if not (CRYPTO_MIN_HOURS <= hours_left <= CRYPTO_MAX_HOURS):
            continue
        yes_price = KalshiClient.yes_price_cents(market)
        if not (CRYPTO_MIN_YES <= yes_price <= CRYPTO_MAX_YES):
            continue
        if direction == "above" and yes_price < 50:
            continue
        market_p = yes_price / 100.0
        our_p    = market_p + CRYPTO_BIAS
        asset_name = "BTC" if asset_raw == "BTC" else "ETH"
        direction_word = "above" if direction == "above" else "below"
        seen_keys.add(close_key)
        candidates.append({
            "source": "crypto", "ticker": ticker,
            "title": asset_name + " stays " + direction_word + " $" + str(int(threshold)),
            "yes_price": yes_price, "market_p": market_p, "our_p": our_p,
            "score": _ev_score(our_p, market_p), "hours_left": hours_left,
            "reasoning": asset_name + " is " + str(round(buf*100,1)) + "% " + direction_word + " threshold",
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Parlay: %d qualifying crypto legs", len(candidates))
    return candidates


# ── Leg picker ────────────────────────────────────────────────────────────────

def _pick_legs(stat_legs, nba_legs, crypto_legs):
    """
    Pick 2 best legs. Priority: player stats first (most predictable).
    Dedup rules:
      - Never two legs from same PLAYER (correlated: bad game = both lose)
      - Never two legs from same game event
    """
    picked = []
    used_players = set()
    used_games = set()

    all_legs = stat_legs + nba_legs + crypto_legs

    for leg in all_legs:
        if len(picked) >= LEGS_TARGET:
            break
        if leg["score"] <= 0:
            continue
        # Player dedup for stat legs
        player = leg.get("player_name", "")
        if player and player in used_players:
            logger.info("Parlay dedup: skipping %s (player already in combo)", player)
            continue
        # Game dedup
        game_id = leg.get("game_key", leg.get("ticker", ""))
        if game_id in used_games:
            logger.info("Parlay dedup: skipping %s (game already in combo)", leg["ticker"])
            continue
        picked.append(leg)
        if player:
            used_players.add(player)
        used_games.add(game_id)

    return picked


# ── Main entry point ──────────────────────────────────────────────────────────

def run_parlay():
    """
    Entry point called by APScheduler at 5pm ET.
    Builds and places a 2-leg combo using best available markets.
    """
    global _RESEARCH_USED
    _RESEARCH_USED = 0  # reset per run

    logger.info("-- Parlay engine scan starting --")

    try:
        client = KalshiClient(
            key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", "/opt/apex/kalshi_private.pem"),
            paper_mode=PAPER_MODE,
        )
    except Exception as e:
        logger.warning("Parlay: client init failed: %s", e)
        return []

    stat_legs   = _get_stat_legs(client)
    nba_legs    = _get_nba_legs(client)
    crypto_legs = _get_crypto_legs(client)
    legs        = _pick_legs(stat_legs, nba_legs, crypto_legs)

    if not legs:
        logger.info("Parlay: no qualifying legs -- nothing placed")
        return []

    # Size each leg
    sized = []
    for leg in legs:
        bet_usd   = _size_bet(leg["our_p"], leg["market_p"])
        contracts = max(1, int(bet_usd / (leg["yes_price"] / 100)))
        cost_usd  = round(contracts * leg["yes_price"] / 100, 2)
        payout    = round(contracts * 1.00, 2)
        profit    = round(payout - cost_usd, 2)
        sized.append({**leg, "bet_usd": bet_usd, "contracts": contracts,
                      "cost_usd": cost_usd, "payout": payout, "profit_if_wins": profit})

    total_cost = round(sum(l["cost_usd"] for l in sized), 2)
    if not _cash_reserve_ok(client, total_cost):
        logger.info("Parlay: SKIP -- cash reserve / drawdown check failed")
        return []

    # Place orders
    orders = []
    for leg in sized:
        ticker    = leg["ticker"]
        yes_price = leg["yes_price"]
        contracts = leg["contracts"]
        cost_usd  = leg["cost_usd"]

        logger.info(
            "PARLAY %s: %s YES=%dc -> BUY YES x%d @%dc cost=$%.2f win=$%.2f",
            leg["source"].upper(), ticker, yes_price,
            contracts, yes_price, cost_usd, leg["profit_if_wins"],
        )

        try:
            result = client.place_limit_order(
                ticker=ticker, side="yes",
                price_cents=yes_price, contracts=contracts,
            )
        except Exception as e:
            logger.error("Parlay order failed %s: %s", ticker, e)
            continue

        entry = {
            "date":        datetime.now(timezone.utc).isoformat(),
            "strategy":    "parlay",
            "source":      leg["source"],
            "ticker":      ticker,
            "title":       leg["title"],
            "side":        "yes",
            "price_cents": yes_price,
            "contracts":   contracts,
            "cost_usd":    cost_usd,
            "paper":       PAPER_MODE,
            "order_id":    result.get("order", {}).get("order_id", ""),
        }
        orders.append({**entry, "profit_if_wins": leg["profit_if_wins"],
                       "our_p": leg["our_p"], "reasoning": leg.get("reasoning","")})
        _log_trade(entry)

    # Telegram: one message, casual and clear
    if orders:
        total_profit = round(sum(o["profit_if_wins"] for o in orders), 2)
        total_paid   = round(sum(o["cost_usd"] for o in orders), 2)

        leg_lines = []
        for i, o in enumerate(orders):
            pct = round(o["our_p"] * 100)
            source_label = {"player_stat": "Player stat", "nba_game": "Game winner",
                            "crypto": "Crypto"}.get(o["source"], o["source"])
            reason = o.get("reasoning", "")
            reason_note = (" -- " + reason[:60]) if reason else ""
            leg_lines.append(
                str(i+1) + ". " + source_label + ": " + o["title"][:50]
                + " -- $" + str(o["cost_usd"]) + " (~" + str(pct) + "% likely)"
                + reason_note
            )

        legs_text = "\n".join(leg_lines)
        msg = (
            "Today's combo is locked in!\n\n"
            + legs_text + "\n\n"
            + "Total in: $" + str(total_paid)
            + " -- if both hit -> +" + "$" + str(total_profit) + " profit"
        )
        asyncio.run(tg.send_message(msg))

    logger.info("-- Parlay engine complete | legs=%d --", len(orders))
    return orders


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s -- %(message)s")
    run_parlay()
