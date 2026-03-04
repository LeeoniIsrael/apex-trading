"""Momentum strategy: cross-sectional momentum with volatility scaling.

Hypothesis: Stocks with the highest 20-day risk-adjusted returns
outperform over the next 5 days. Position size is scaled by inverse
volatility (Kelly-inspired).
"""

from __future__ import annotations

import pandas as pd

from src.strategy.features import add_momentum_features


def rank_universe(
    prices: dict[str, pd.DataFrame],
    lookback: int = 20,
    top_n: int = 5,
) -> list[tuple[str, float]]:
    """Rank symbols by risk-adjusted momentum. Returns top N as (symbol, score)."""
    scores: dict[str, float] = {}

    for symbol, df in prices.items():
        df = add_momentum_features(df)
        if df["return_20d"].isna().all() or df["volatility_20d"].isna().all():
            continue
        ret = df["return_20d"].iloc[-1]
        vol = df["volatility_20d"].iloc[-1]
        if vol and vol > 0:
            scores[symbol] = ret / vol

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_n]


def generate_signals(
    prices: dict[str, pd.DataFrame],
    current_positions: set[str],
    top_n: int = 5,
) -> dict[str, str]:
    """Return {symbol: 'BUY'|'SELL'|'HOLD'} for the full universe."""
    top = {sym for sym, _ in rank_universe(prices, top_n=top_n)}
    signals: dict[str, str] = {}

    all_symbols = set(prices.keys()) | current_positions
    for symbol in all_symbols:
        if symbol in top and symbol not in current_positions:
            signals[symbol] = "BUY"
        elif symbol not in top and symbol in current_positions:
            signals[symbol] = "SELL"
        else:
            signals[symbol] = "HOLD"

    return signals
