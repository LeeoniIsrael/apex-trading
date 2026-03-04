"""Feature engineering for ML signal generation."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_momentum_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add momentum-based features to a OHLCV DataFrame.

    Expects columns: open, high, low, close, volume.
    Returns df with additional feature columns.
    """
    df = df.copy()

    # Price momentum
    for window in [5, 10, 20, 60]:
        df[f"return_{window}d"] = df["close"].pct_change(window)

    # Volatility
    df["volatility_20d"] = df["close"].pct_change().rolling(20).std()
    df["volatility_60d"] = df["close"].pct_change().rolling(60).std()

    # Volume features
    df["volume_ratio_20d"] = df["volume"] / df["volume"].rolling(20).mean()

    # RSI
    df["rsi_14"] = _rsi(df["close"], period=14)

    # ATR
    df["atr_14"] = _atr(df["high"], df["low"], df["close"], period=14)

    # Moving average crossover
    df["sma_20"] = df["close"].rolling(20).mean()
    df["sma_50"] = df["close"].rolling(50).mean()
    df["ma_cross"] = (df["sma_20"] / df["sma_50"] - 1)

    return df


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean()


FEATURE_COLUMNS = [
    "return_5d",
    "return_10d",
    "return_20d",
    "return_60d",
    "volatility_20d",
    "volatility_60d",
    "volume_ratio_20d",
    "rsi_14",
    "atr_14",
    "ma_cross",
]
