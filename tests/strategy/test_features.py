"""Tests for feature engineering."""

import numpy as np
import pandas as pd
import pytest

from src.strategy.features import FEATURE_COLUMNS, add_momentum_features


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    np.random.seed(42)
    n = 100
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.random.randint(1_000_000, 5_000_000, n),
        }
    )


def test_add_momentum_features_returns_all_columns(sample_ohlcv):
    result = add_momentum_features(sample_ohlcv)
    for col in FEATURE_COLUMNS:
        assert col in result.columns, f"Missing feature column: {col}"


def test_add_momentum_features_does_not_mutate_input(sample_ohlcv):
    original_cols = list(sample_ohlcv.columns)
    add_momentum_features(sample_ohlcv)
    assert list(sample_ohlcv.columns) == original_cols


def test_rsi_bounded(sample_ohlcv):
    result = add_momentum_features(sample_ohlcv)
    rsi = result["rsi_14"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_feature_row_count_preserved(sample_ohlcv):
    result = add_momentum_features(sample_ohlcv)
    assert len(result) == len(sample_ohlcv)
