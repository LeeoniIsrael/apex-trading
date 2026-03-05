"""Claude-powered reasoning layer for the trading agent.

Uses claude-haiku-4-5 for tick-level decisions (cost efficiency).
Uses claude-sonnet-4-6 for end-of-day analysis.

LightGBM gate: regime-conditional.
- In 'choppy' regime (SPY 20d vol > 1.5x its 60d average): gate is ACTIVE.
  BUY signals are blocked if LightGBM proba < LGBM_THRESHOLD.
- In 'trending' regime: gate is OPEN. All momentum BUYs pass through to Claude.

This prevents the filter from reducing alpha in trending bull markets while
still providing noise-rejection during choppy/uncertain conditions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

import anthropic
import pandas as pd

from src.config import settings
from src.strategy.features import LGBMSignalModel, build_training_dataset

logger = logging.getLogger(__name__)

LGBM_THRESHOLD   = 0.55   # minimum LightGBM probability to allow a BUY (choppy regime)
VOL_RATIO_THRESH = 1.5    # 20d vol / 60d-avg-vol threshold defining 'choppy'

_client: anthropic.Anthropic | None = None
_lgbm_model: LGBMSignalModel | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def init_lgbm_filter(price_data: dict[str, pd.DataFrame]) -> None:
    """Train and cache the LightGBM filter from historical price data.

    Call once at agent startup before the trading loop begins.
    price_data: {symbol: OHLCV DataFrame} covering the training window.
    """
    global _lgbm_model
    logger.info("Training LightGBM confirmation filter…")
    dataset = build_training_dataset(price_data)
    model = LGBMSignalModel(n_splits=5)
    model.fit(dataset)
    _lgbm_model = model
    logger.info(
        "LightGBM filter ready. Mean CV AUC: %.4f",
        sum(model.cv_scores_) / len(model.cv_scores_),
    )


def get_market_regime(spy_close: pd.Series) -> Literal["trending", "choppy"]:
    """Classify current market regime using SPY volatility ratio.

    Definition:
      20d_vol  = rolling 20-day std of SPY daily returns (latest value)
      60d_avg  = rolling 60-day mean of that 20d_vol series (latest value)
      regime   = 'choppy'   if 20d_vol > VOL_RATIO_THRESH * 60d_avg
               = 'trending' otherwise

    Defaults to 'trending' (gate open) when there is insufficient data or
    the calculation cannot be completed.
    """
    returns = spy_close.ffill().pct_change(fill_method=None).dropna()
    if len(returns) < 80:
        logger.debug("get_market_regime: insufficient SPY data (%d rows) — default trending", len(returns))
        return "trending"

    vol_20d     = returns.rolling(20).std()
    vol_60d_avg = vol_20d.rolling(60).mean()

    current_vol = vol_20d.iloc[-1]
    avg_vol     = vol_60d_avg.iloc[-1]

    if pd.isna(current_vol) or pd.isna(avg_vol) or avg_vol == 0:
        logger.debug("get_market_regime: NaN or zero in vol calculation — default trending")
        return "trending"

    ratio  = current_vol / avg_vol
    regime: Literal["trending", "choppy"] = "choppy" if ratio > VOL_RATIO_THRESH else "trending"
    logger.info(
        "Market regime: %s (20d_vol=%.4f, 60d_avg=%.4f, ratio=%.2fx)",
        regime.upper(), current_vol, avg_vol, ratio,
    )
    return regime


def apply_lgbm_gate(symbol: str, df: pd.DataFrame) -> tuple[float, bool]:
    """Check LightGBM confirmation for the latest row of df.

    Returns (proba, passes) where passes=True means LightGBM agrees with BUY.
    If the model is not yet trained, passes defaults to True (no blocking).
    """
    if _lgbm_model is None:
        logger.debug("LightGBM filter not initialised — gate open by default")
        return (1.0, True)

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    from src.strategy.features import add_momentum_features
    df = add_momentum_features(df)
    latest = df.dropna().tail(1)
    if latest.empty:
        logger.warning("apply_lgbm_gate: insufficient data for %s — gate open", symbol)
        return (1.0, True)

    proba = float(_lgbm_model.predict_proba(latest)[0])
    passes = proba >= LGBM_THRESHOLD
    logger.debug("LGBM gate %s: proba=%.3f passes=%s", symbol, proba, passes)
    return (proba, passes)


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
- lgbm_proba in market_context is the LightGBM confirmation probability (>= 0.55 means ML agrees)
- market_regime in market_context is 'trending' (gate open) or 'choppy' (gate active)
"""


def evaluate_signal(
    symbol: str,
    strategy_signal: str,
    market_context: dict,
    portfolio_state: dict,
    df: pd.DataFrame | None = None,
    spy_close: pd.Series | None = None,
) -> dict:
    """Evaluate a trading signal with regime-conditional LightGBM gate + Claude reasoning.

    Gate behaviour for BUY signals:
      - 'trending' regime (SPY vol normal):  gate OPEN — BUY goes straight to Claude.
      - 'choppy'  regime (SPY vol elevated): gate ACTIVE — blocked if proba < LGBM_THRESHOLD.

    SELL/HOLD signals always bypass the gate — exits are never blocked.
    """
    lgbm_proba: float = 1.0
    regime = "trending"

    if strategy_signal.upper() == "BUY":
        # Determine regime first
        if spy_close is not None:
            regime = get_market_regime(spy_close)
        else:
            logger.debug("evaluate_signal: no spy_close provided — regime defaults to 'trending'")

        # Apply gate only in choppy regime
        if regime == "choppy" and df is not None:
            lgbm_proba, passes = apply_lgbm_gate(symbol, df)
            if not passes:
                logger.info(
                    "LGBM gate BLOCKED %s BUY (choppy regime) — proba=%.3f < threshold=%.2f",
                    symbol, lgbm_proba, LGBM_THRESHOLD,
                )
                return {
                    "action": "HOLD",
                    "confidence": lgbm_proba,
                    "reasoning": (
                        f"LightGBM gate blocked entry in choppy regime: "
                        f"proba={lgbm_proba:.3f} < threshold={LGBM_THRESHOLD}. "
                        "Momentum signal present but ML does not confirm under elevated volatility."
                    ),
                    "risk_factors": ["lgbm_confirmation_failed", "choppy_regime"],
                }
        elif regime == "trending":
            logger.debug("LGBM gate OPEN for %s — trending regime, no filter applied", symbol)

    market_context = {
        **market_context,
        "lgbm_proba": round(lgbm_proba, 4),
        "market_regime": regime,
    }

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
