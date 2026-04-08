"""
Claude Haiku decision engine for APEX prediction market agent.
Analyzes Kalshi markets and returns structured trade recommendations.
"""
import json
import logging
import os
import re as _re
import time
from pathlib import Path
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MIN_EDGE = 0.07  # 7% minimum edge (raised per favourite-longshot bias research)
MARKET_INTEL_PATH = Path(__file__).parent / "market_intel.json"

# Change 3: Category guardrails.
# BLOCKED_CATEGORIES: no algorithmic edge exists — skip without any Claude call.
# PREFERRED_CATEGORIES: use for future scoring/prioritisation logic.
BLOCKED_CATEGORIES = frozenset({
    "crypto",         # price bracket structure makes systematic betting unprofitable
    "crypto_bracket", # explicit sub-type
    "entertainment",  # no reliable signal for algorithmic trading
    "culture",        # no reliable signal for algorithmic trading
})
PREFERRED_CATEGORIES = frozenset({
    "sports",
    "weather",
    "economics",
})

# Change 5: Fee-optimized price filter.
# Kalshi fees are highest (3–7%) for contracts priced 40¢–60¢.
# Only bet contracts priced < 35¢ or > 65¢ to preserve edge.
FEE_TRAP_LOW  = 40
FEE_TRAP_HIGH = 60

SYSTEM_PROMPT = """You are APEX, an autonomous prediction market trading agent.
Your job is to analyze Kalshi prediction market questions and determine if there is a
genuine betting edge based on current evidence.

Always:
1. Search for recent news about the specific market question
2. Estimate the TRUE probability based on evidence found
3. Compare to the MARKET IMPLIED probability (from price)
4. Only recommend a trade if edge > 7%
5. Be conservative — "SKIP" is always safe

Important: Research shows Kalshi has a favourite-longshot bias. Avoid recommending \
bets under 25 cents (longshots lose more than implied). Prefer high-confidence markets \
priced between 55-85 cents where the edge is most reliable. Only recommend BUY when \
your estimated probability exceeds market price by at least 7%.

Prioritize markets that resolve within 24 hours. Crypto price markets, economic data \
releases, and sports games all qualify. Spread bets across different categories — do \
not place more than 3 bets in any single category per scan cycle.

Sports markets: when a team is a heavy favourite (YES price 75¢ or higher), \
prefer buying NO on the underdog outcome rather than YES on the favourite. \
Heavy favourites are systematically overpriced on Kalshi — the NO side offers \
better expected value. Only deviate from this if you find very strong specific \
evidence (injury news, lineup changes) that confirms the favourite.

Return ONLY valid JSON, no markdown, no explanation outside the JSON.
"""

MARKET_PROMPT = """Analyze this Kalshi prediction market:

Market: {title}
Ticker: {ticker}
YES price: {yes_price} cents (implied prob: {yes_prob:.1%})
NO price: {no_price} cents (implied prob: {no_prob:.1%})
Volume: {volume} contracts
Close time: {close_time}
Category: {category}

Steps:
1. Use web_search to find recent news about this specific question
2. Estimate the true probability of YES outcome
3. Calculate edge = our_probability - market_implied_probability

Return JSON with exactly these fields:
{{
  "action": "BUY_YES" | "BUY_NO" | "SKIP",
  "our_probability": <float 0-1>,
  "market_probability": <float 0-1>,
  "edge": <float, positive means bet YES, negative means bet NO>,
  "confidence": <float 0-1>,
  "reasoning": "<1-2 sentence explanation>"
}}

Only return BUY_YES or BUY_NO if |edge| > 0.07 AND confidence > 0.6 AND market price is between 25-85 cents.
"""


_CRYPTO_KEYWORDS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
    "doge", "xrp", "ripple",
)
_WEATHER_KEYWORDS = (
    "temperature", "temp", "high", "low", "degrees", "fahrenheit", "°f",
    "kxhigh", "kxtemp", "weather",
)


def _needs_web_search(title: str, category: str) -> tuple[bool, str]:
    """
    Return (True, '') if web_search is worth calling, or (False, reason) to skip.

    Skip for:
    - Crypto / Bitcoin price bracket markets — price is already in the market.
    - Weather temperature bracket markets — GFS model data is the signal, not news.

    Use web_search for sports, politics, economics, and everything else where
    recent news genuinely shifts the probability.
    """
    title_lower = title.lower()
    cat_lower = category.lower()

    if any(k in title_lower or k in cat_lower for k in _CRYPTO_KEYWORDS):
        return False, "crypto price market — news doesn't add signal"

    if any(k in title_lower or k in cat_lower for k in _WEATHER_KEYWORDS):
        return False, "weather temperature market — GFS model is the signal"

    return True, ""


def _load_market_intel() -> dict:
    """Load market intelligence if fresh (< 60 min old)."""
    try:
        if not MARKET_INTEL_PATH.exists():
            return {}
        if time.time() - MARKET_INTEL_PATH.stat().st_mtime > 3600:
            return {}
        return json.loads(MARKET_INTEL_PATH.read_text())
    except Exception:
        return {}


def analyze_market(market: dict[str, Any]) -> dict[str, Any]:
    """
    Analyze a Kalshi market dict and return a trade recommendation.

    Args:
        market: Kalshi market object from API

    Returns:
        dict with keys: action, our_probability, market_probability, edge,
                        confidence, reasoning
    """
    from kalshi_client import KalshiClient

    ticker_raw   = market.get("ticker", "")
    title_raw    = market.get("_event_title") or market.get("title", "Unknown")
    category_raw = (market.get("_event_category") or market.get("category", "")).lower()

    # Change 1/3: Skip crypto price bracket markets entirely — structural loss risk
    # (also caught by BLOCKED_CATEGORIES but keeping the keyword check as belt-and-braces)
    if any(
        k in title_raw.lower() or k in category_raw or k in ticker_raw.lower()
        for k in _CRYPTO_KEYWORDS
    ):
        logger.info("SKIP %s — crypto bracket, structural loss risk", ticker_raw)
        return _skip_result("crypto bracket, structural loss risk")

    # Change 3: Hard category block — no Claude API call, no cost
    if category_raw in BLOCKED_CATEGORIES:
        logger.info("HARD BLOCK — category [%s] blocked from trading [%s]", category_raw, ticker_raw)
        return _skip_result(f"HARD BLOCK — category [{category_raw}] blocked from trading")

    yes_price = KalshiClient.yes_price_cents(market)

    # Change 5: Fee trap filter — avoid mid-range contracts where fees eat edge
    if FEE_TRAP_LOW <= yes_price <= FEE_TRAP_HIGH:
        logger.info("SKIP %s — mid-range fee trap (40-60¢) price=%d¢", ticker_raw, yes_price)
        return _skip_result("mid-range fee trap (40-60¢)")

    no_price = 100 - yes_price
    title    = title_raw
    category = category_raw
    volume   = market.get("volume_fp") or market.get("volume", 0)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    prompt = MARKET_PROMPT.format(
        title=title,
        ticker=ticker_raw,
        yes_price=yes_price,
        no_price=no_price,
        yes_prob=yes_price / 100,
        no_prob=no_price / 100,
        volume=volume,
        close_time=market.get("close_time", "unknown"),
        category=category,
    )

    use_search, skip_reason = _needs_web_search(title, category)
    if not use_search:
        logger.info("SKIP web_search — %s [%s]", skip_reason, ticker_raw)

    # Inject live calibration data into system prompt
    try:
        import feedback_loop as _fb
        calibration = _fb.get_edge_calibration()
    except Exception:
        calibration = ""
    system_prompt = SYSTEM_PROMPT
    if calibration:
        system_prompt = SYSTEM_PROMPT + "\n\n" + calibration

    # Enrich prompt with market intelligence if available
    intel = _load_market_intel()
    if intel:
        headlines = intel.get("news_headlines", [])[:3]
        whales = intel.get("polymarket_whale_moves", [])[:2]
        intel_lines = []
        if headlines:
            intel_lines.append("Recent prediction market news:")
            for h in headlines:
                if h.get("title"):
                    intel_lines.append(f"- {h['title']}")
        if whales:
            intel_lines.append("Polymarket whale moves (large recent trades):")
            for w in whales:
                intel_lines.append(
                    f"- {w.get('outcome', '')} on {str(w.get('market', ''))[:50]}: "
                    f"${float(w.get('size_usd') or 0):.0f} at {float(w.get('price') or 0):.0%}"
                )
        if intel_lines:
            prompt += "\n\nAdditional market context:\n" + "\n".join(intel_lines)

    tools = (
        [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
        if use_search
        else []
    )
    no_search_note = (
        "\n\nNote: no web search available. Use your training knowledge."
        if not use_search
        else ""
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=system_prompt,
            **({"tools": tools} if tools else {}),
            messages=[{"role": "user", "content": prompt + no_search_note}],
        )

        # Extract the text content from the response.
        # Claude may return tool_use blocks followed by a text block, or text only.
        result_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                result_text += block.text

        if not result_text.strip():
            # Claude returned only tool_use blocks — retry without web_search
            logger.warning(
                "No text block from Claude for %s — retrying without web_search",
                ticker_raw,
            )
            retry_response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": prompt + "\n\nNote: no web search available. Use your training knowledge.",
                }],
            )
            result_text = ""
            for block in retry_response.content:
                if hasattr(block, "text"):
                    result_text += block.text

        if not result_text.strip():
            logger.warning("Empty response from Claude for market %s", ticker_raw)
            return _skip_result("Empty Claude response")

        # Parse JSON — strip markdown fences and find first {...} block
        cleaned = result_text.strip()

        if "```" in cleaned:
            m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, _re.DOTALL)
            if m:
                cleaned = m.group(1)

        if not cleaned.startswith("{"):
            m = _re.search(r"\{[^{}]*\"action\"[^{}]*\}", cleaned, _re.DOTALL)
            if m:
                cleaned = m.group(0)
            else:
                logger.warning(
                    "No JSON object found in response for %s — text: %s",
                    ticker_raw, cleaned[:200],
                )
                return _skip_result("No JSON in Claude response")

        result = json.loads(cleaned)

        # Validate and enforce minimum edge
        edge = abs(float(result.get("edge", 0)))
        confidence = float(result.get("confidence", 0))

        if edge < MIN_EDGE or confidence < 0.6:
            result["action"] = "SKIP"
            result["reasoning"] = (
                f"Edge {edge:.1%} or confidence {confidence:.0%} below threshold. "
                + result.get("reasoning", "")
            )

        logger.info(
            "brain.analyze_market | ticker=%s action=%s edge=%.3f confidence=%.2f",
            ticker_raw, result.get("action"), edge, confidence,
        )
        return result

    except Exception as e:
        logger.error("brain.analyze_market failed for %s: %s", ticker_raw, e)
        return _skip_result(str(e))


def _skip_result(reason: str) -> dict:
    return {
        "action": "SKIP",
        "our_probability": 0.5,
        "market_probability": 0.5,
        "edge": 0.0,
        "confidence": 0.0,
        "reasoning": reason,
    }
