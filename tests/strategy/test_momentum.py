"""Tests for momentum strategy signal generation."""

import numpy as np
import pandas as pd
import pytest

from src.strategy.momentum import generate_signals, rank_universe


def _make_price_df(seed: int, n: int = 100, trend: float = 0.5) -> pd.DataFrame:
    np.random.seed(seed)
    close = 100 + np.cumsum(np.random.randn(n) * trend)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.random.randint(1_000_000, 5_000_000, n),
        }
    )


@pytest.fixture
def universe() -> dict[str, pd.DataFrame]:
    return {
        "AAPL": _make_price_df(1, trend=1.0),   # strong uptrend
        "MSFT": _make_price_df(2, trend=0.5),
        "GOOGL": _make_price_df(3, trend=0.1),
        "AMZN": _make_price_df(4, trend=-0.5),  # downtrend
        "TSLA": _make_price_df(5, trend=-1.0),  # strong downtrend
    }


def test_rank_universe_returns_top_n(universe):
    ranked = rank_universe(universe, top_n=2)
    assert len(ranked) == 2


def test_rank_universe_returns_tuples(universe):
    ranked = rank_universe(universe, top_n=3)
    for item in ranked:
        assert isinstance(item, tuple)
        assert isinstance(item[0], str)
        assert isinstance(item[1], float)


def test_generate_signals_keys(universe):
    signals = generate_signals(universe, current_positions=set())
    assert set(signals.keys()) == set(universe.keys())


def test_generate_signals_valid_values(universe):
    signals = generate_signals(universe, current_positions=set())
    for val in signals.values():
        assert val in ("BUY", "SELL", "HOLD")


def test_generate_signals_sells_dropped_positions(universe):
    # TSLA is the worst performer — should be dropped from positions
    signals = generate_signals(universe, current_positions={"TSLA"}, top_n=2)
    assert signals.get("TSLA") == "SELL"
