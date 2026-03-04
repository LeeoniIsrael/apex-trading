"""Mean reversion strategy: Bollinger Band reversion.

Hypothesis: Stocks trading >2 standard deviations below their 20-day
moving average tend to revert within 5 days. Exit at the mean or
at the stop-loss.
"""

from __future__ import annotations

import pandas as pd


def bollinger_signals(
    df: pd.DataFrame,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.Series:
    """Return a signal series: 1=BUY (oversold), -1=SELL (overbought), 0=HOLD."""
    close = df["close"]
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std

    signal = pd.Series(0, index=df.index)
    signal[close < lower] = 1
    signal[close > upper] = -1
    return signal


def generate_signals(
    prices: dict[str, pd.DataFrame],
    current_positions: set[str],
    window: int = 20,
    num_std: float = 2.0,
) -> dict[str, str]:
    """Return {symbol: 'BUY'|'SELL'|'HOLD'} for the universe."""
    signals: dict[str, str] = {}

    for symbol, df in prices.items():
        sig_series = bollinger_signals(df, window=window, num_std=num_std)
        latest = sig_series.iloc[-1] if len(sig_series) > 0 else 0

        if latest == 1 and symbol not in current_positions:
            signals[symbol] = "BUY"
        elif latest == -1 and symbol in current_positions:
            signals[symbol] = "SELL"
        else:
            signals[symbol] = "HOLD"

    return signals
