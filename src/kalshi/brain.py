"""
Claude Haiku decision engine for APEX prediction market agent.
Analyzes Kalshi markets and returns structured trade recommendations.
"""
import logging
import os
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MIN_EDGE = 0.05  # 5% minimum edge to recommend a trade

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


def analyze_market(market: dict[str, Any]) -> dict[str, Any]:
    """
    Analyze a Kalshi market dict and return a trade recommendation.

    Args:
        market: Kalshi market object from API

    Returns:
        dict with keys: action, our_probability, market_probability, edge,
                        confidence, reasoning
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    yes_price = market.get("yes_ask", market.get("yes_bid", 50))
    no_price = 100 - yes_price

    prompt = MARKET_PROMPT.format(
        title=market.get("title", "Unknown"),
        ticker=market.get("ticker", ""),
        yes_price=yes_price,
        no_price=no_price,
        yes_prob=yes_price / 100,
        no_prob=no_price / 100,
        volume=market.get("volume", 0),
        close_time=market.get("close_time", "unknown"),
        category=market.get("category", "unknown"),
    )

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
        result_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                result_text += block.text

        if not result_text.strip():
            logger.warning("Empty response from Claude for market %s", market.get("ticker"))
            return _skip_result("Empty Claude response")

        import json
        # Strip any markdown code fences if present
        cleaned = result_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()

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
