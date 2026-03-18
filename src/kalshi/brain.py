"""
Claude Haiku decision engine for APEX prediction market agent.
Analyzes Kalshi markets and returns structured trade recommendations.
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MIN_EDGE = 0.05  # 5% minimum edge to recommend a trade
MARKET_INTEL_PATH = Path(__file__).parent / "market_intel.json"

SYSTEM_PROMPT = """You are APEX, an autonomous prediction market trading agent.
Your job is to analyze Kalshi prediction market questions and determine if there is a
genuine betting edge based on current evidence.

Always:
1. Search for recent news about the specific market question
2. Estimate the TRUE probability based on evidence found
3. Compare to the MARKET IMPLIED probability (from price)
4. Only recommend a trade if edge > 5%
5. Be conservative — "SKIP" is always safe

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

Only return BUY_YES or BUY_NO if |edge| > 0.05 AND confidence > 0.6.
"""


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
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    yes_price = KalshiClient.yes_price_cents(market)
    no_price = 100 - yes_price

    # title: prefer event title injected by get_markets, fall back to market title
    title = market.get("_event_title") or market.get("title", "Unknown")
    category = market.get("_event_category") or market.get("category", "unknown")
    volume = market.get("volume_fp") or market.get("volume", 0)

    prompt = MARKET_PROMPT.format(
        title=title,
        ticker=market.get("ticker", ""),
        yes_price=yes_price,
        no_price=no_price,
        yes_prob=yes_price / 100,
        no_prob=no_price / 100,
        volume=volume,
        close_time=market.get("close_time", "unknown"),
        category=category,
    )

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

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 2,
                }
            ],
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract the text content from the response
        # Claude may return tool_use blocks followed by a text block, or text only.
        # Collect all text blocks; if none exist, the model returned only tool calls.
        result_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                result_text += block.text

        if not result_text.strip():
            # Claude returned only tool_use blocks (web search) with no final text.
            # This happens when the model decides the search result IS the answer.
            # Retry without web_search so it must synthesize a text response.
            logger.warning(
                "No text block from Claude for %s — retrying without web_search",
                market.get("ticker"),
            )
            retry_response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=SYSTEM_PROMPT,
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
            logger.warning("Empty response from Claude for market %s", market.get("ticker"))
            return _skip_result("Empty Claude response")

        import json, re as _re

        # 1. Try to extract JSON object from anywhere in the text
        cleaned = result_text.strip()

        # Strip markdown fences
        if "```" in cleaned:
            m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, _re.DOTALL)
            if m:
                cleaned = m.group(1)

        # If still not a bare JSON object, find the first {...} block
        if not cleaned.startswith("{"):
            m = _re.search(r"\{[^{}]*\"action\"[^{}]*\}", cleaned, _re.DOTALL)
            if m:
                cleaned = m.group(0)
            else:
                logger.warning(
                    "No JSON object found in response for %s — text: %s",
                    market.get("ticker"), cleaned[:200],
                )
                return _skip_result("No JSON in Claude response")

        result = json.loads(cleaned)

        # Validate and enforce minimum edge
        action = result.get("action", "SKIP")
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
            market.get("ticker"), result.get("action"), edge, confidence,
        )
        return result

    except Exception as e:
        logger.error("brain.analyze_market failed for %s: %s", market.get("ticker"), e)
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
