"""Claude-powered reasoning layer for the trading agent.

Uses claude-haiku-4-5 for tick-level decisions (cost efficiency).
Uses claude-sonnet-4-6 for end-of-day analysis.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import anthropic

from src.config import settings

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


DECISION_SYSTEM_PROMPT = """You are APEX, an autonomous equity trading agent.
Your job is to evaluate trading signals and decide whether to act on them.

You must respond with a JSON object with this exact structure:
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0–1.0,
  "reasoning": "concise explanation (1-3 sentences)",
  "risk_factors": ["list of key risks"]
}

Rules:
- Never exceed the position limits set in your configuration
- Always consider current market regime (trending vs. mean-reverting)
- Prefer HOLD when confidence < 0.6
- Cite specific data points in your reasoning
"""


def evaluate_signal(
    symbol: str,
    strategy_signal: str,
    market_context: dict,
    portfolio_state: dict,
) -> dict:
    """Ask Claude haiku to evaluate a signal and return a structured decision."""
    prompt = f"""
Symbol: {symbol}
Strategy signal: {strategy_signal}
Market context: {json.dumps(market_context, indent=2)}
Portfolio state: {json.dumps(portfolio_state, indent=2)}

Evaluate this trading signal. Return your decision as JSON.
"""
    client = _get_client()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=DECISION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Claude returned non-JSON response: %s", text)
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "reasoning": "Parse error — defaulting to HOLD",
            "risk_factors": ["response_parse_failure"],
        }


EOD_ANALYSIS_SYSTEM_PROMPT = """You are APEX, an autonomous equity trading agent.
At end-of-day, you write a structured analysis of trading performance.
Respond in Markdown. Be concise but insightful. Include:
1. Summary of decisions made today
2. What worked and what didn't
3. Key market observations
4. Plan for tomorrow
"""


def end_of_day_analysis(
    trades_today: list[dict],
    portfolio_snapshot: dict,
    market_summary: dict,
) -> str:
    """Generate end-of-day analysis using claude-sonnet-4-6."""
    prompt = f"""
Today's date: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}
Trades executed: {json.dumps(trades_today, indent=2)}
Portfolio snapshot: {json.dumps(portfolio_snapshot, indent=2)}
Market summary: {json.dumps(market_summary, indent=2)}

Write the end-of-day analysis.
"""
    client = _get_client()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=EOD_ANALYSIS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
