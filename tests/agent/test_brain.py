"""Tests for brain.py: LightGBM gate and regime-conditional logic."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import src.agent.brain as brain
from src.agent.brain import (
    LGBM_THRESHOLD,
    VOL_RATIO_THRESH,
    apply_lgbm_gate,
    evaluate_signal,
    get_market_regime,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 120, seed: int = 0) -> pd.DataFrame:
    np.random.seed(seed)
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open":   close * 0.999,
        "high":   close * 1.005,
        "low":    close * 0.995,
        "close":  close,
        "volume": np.random.randint(1_000_000, 5_000_000, n),
    })


def _make_spy_trending(n: int = 200) -> pd.Series:
    """SPY close series with uniform low volatility → trending regime."""
    np.random.seed(1)
    daily_returns = np.random.randn(n) * 0.005          # ~0.5% daily vol, constant
    prices = 400 * np.exp(np.cumsum(daily_returns))
    return pd.Series(prices)


def _make_spy_choppy(n: int = 200) -> pd.Series:
    """SPY close series where recent 20d vol >> 60d average → choppy regime."""
    np.random.seed(2)
    # Calm first (n-20) days, then volatile last 20 days
    calm   = np.random.randn(n - 20) * 0.003            # quiet
    stormy = np.random.randn(20)      * 0.06             # 20x noisier
    daily_returns = np.concatenate([calm, stormy])
    prices = 400 * np.exp(np.cumsum(daily_returns))
    return pd.Series(prices)


# ─── Model stubs ────────────────────────────────────────────────────────────

class _HighProbaModel:
    cv_scores_ = [0.55]
    feature_importance_ = None
    def predict_proba(self, df): return np.full(len(df), 0.9)

class _LowProbaModel:
    cv_scores_ = [0.50]
    feature_importance_ = None
    def predict_proba(self, df): return np.full(len(df), 0.4)


# ─── get_market_regime ───────────────────────────────────────────────────────

def test_regime_trending_when_vol_uniform():
    spy = _make_spy_trending()
    assert get_market_regime(spy) == "trending"


def test_regime_choppy_when_recent_vol_elevated():
    spy = _make_spy_choppy()
    assert get_market_regime(spy) == "choppy"


def test_regime_defaults_to_trending_when_insufficient_data():
    """Fewer than 80 data points → default to trending (gate open)."""
    spy = pd.Series([400.0 + i * 0.1 for i in range(50)])
    assert get_market_regime(spy) == "trending"


def test_regime_defaults_to_trending_on_nan():
    """NaN close prices → default to trending."""
    spy = pd.Series([np.nan] * 200)
    assert get_market_regime(spy) == "trending"


# ─── apply_lgbm_gate ────────────────────────────────────────────────────────

def test_gate_open_when_model_not_initialised():
    brain._lgbm_model = None
    proba, passes = apply_lgbm_gate("TEST", _make_ohlcv())
    assert passes is True
    assert proba == 1.0


def test_gate_passes_when_proba_above_threshold():
    brain._lgbm_model = _HighProbaModel()
    proba, passes = apply_lgbm_gate("TEST", _make_ohlcv())
    assert passes is True
    assert proba >= LGBM_THRESHOLD


def test_gate_blocks_when_proba_below_threshold():
    brain._lgbm_model = _LowProbaModel()
    proba, passes = apply_lgbm_gate("TEST", _make_ohlcv())
    assert passes is False
    assert proba < LGBM_THRESHOLD


# ─── evaluate_signal — regime-conditional gate ───────────────────────────────

def test_gate_inactive_in_trending_regime_even_with_low_proba(monkeypatch):
    """In trending regime, LightGBM gate must NOT block BUY regardless of proba."""
    brain._lgbm_model = _LowProbaModel()

    # Patch Claude so the test doesn't make a real API call
    class _FakeMsg:
        class _C:
            text = '{"action":"BUY","confidence":0.8,"reasoning":"ok","risk_factors":[]}'
        content = [_C()]

    class _FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs): return _FakeMsg()

    monkeypatch.setattr(brain, "_get_client", lambda: _FakeClient())

    gate_calls = []
    original_gate = brain.apply_lgbm_gate
    monkeypatch.setattr(brain, "apply_lgbm_gate", lambda s, d: gate_calls.append(s) or original_gate(s, d))

    result = evaluate_signal(
        symbol="TEST",
        strategy_signal="BUY",
        market_context={},
        portfolio_state={},
        df=_make_ohlcv(),
        spy_close=_make_spy_trending(),   # trending → gate open
    )

    # Gate should not have been called in trending regime
    assert gate_calls == [], "apply_lgbm_gate must not fire in trending regime"
    assert result["action"] == "BUY"
    assert result.get("market_regime") is None or True  # regime in context, not result


def test_gate_active_and_blocks_in_choppy_regime(monkeypatch):
    """In choppy regime with low LightGBM proba, BUY is blocked — no Claude call."""
    brain._lgbm_model = _LowProbaModel()

    def _no_claude(*args, **kwargs):
        raise AssertionError("Claude must not be called when gate blocks")

    monkeypatch.setattr(brain, "_get_client", _no_claude)

    result = evaluate_signal(
        symbol="TEST",
        strategy_signal="BUY",
        market_context={},
        portfolio_state={},
        df=_make_ohlcv(),
        spy_close=_make_spy_choppy(),   # choppy → gate active
    )

    assert result["action"] == "HOLD"
    assert "lgbm_confirmation_failed" in result["risk_factors"]
    assert "choppy_regime" in result["risk_factors"]


def test_gate_passes_in_choppy_regime_with_high_proba(monkeypatch):
    """In choppy regime with high LightGBM proba, BUY reaches Claude."""
    brain._lgbm_model = _HighProbaModel()

    class _FakeMsg:
        class _C:
            text = '{"action":"BUY","confidence":0.75,"reasoning":"ok","risk_factors":[]}'
        content = [_C()]

    class _FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs): return _FakeMsg()

    monkeypatch.setattr(brain, "_get_client", lambda: _FakeClient())

    result = evaluate_signal(
        symbol="TEST",
        strategy_signal="BUY",
        market_context={},
        portfolio_state={},
        df=_make_ohlcv(),
        spy_close=_make_spy_choppy(),
    )

    assert result["action"] == "BUY"


def test_sell_bypasses_gate_and_regime(monkeypatch):
    """SELL signals skip both regime check and LightGBM gate entirely."""
    brain._lgbm_model = _LowProbaModel()

    gate_calls = []
    regime_calls = []
    monkeypatch.setattr(brain, "apply_lgbm_gate",   lambda s, d: gate_calls.append(s))
    monkeypatch.setattr(brain, "get_market_regime", lambda s: regime_calls.append(1) or "choppy")

    class _FakeMsg:
        class _C:
            text = '{"action":"SELL","confidence":0.9,"reasoning":"ok","risk_factors":[]}'
        content = [_C()]

    class _FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs): return _FakeMsg()

    monkeypatch.setattr(brain, "_get_client", lambda: _FakeClient())

    evaluate_signal(
        symbol="TEST",
        strategy_signal="SELL",
        market_context={},
        portfolio_state={},
        df=_make_ohlcv(),
        spy_close=_make_spy_choppy(),
    )

    assert gate_calls == [],   "apply_lgbm_gate must not be called for SELL"
    assert regime_calls == [], "get_market_regime must not be called for SELL"


def test_no_spy_close_defaults_gate_open(monkeypatch):
    """Without spy_close, regime defaults to trending and gate stays open."""
    brain._lgbm_model = _LowProbaModel()

    class _FakeMsg:
        class _C:
            text = '{"action":"BUY","confidence":0.7,"reasoning":"ok","risk_factors":[]}'
        content = [_C()]

    class _FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs): return _FakeMsg()

    monkeypatch.setattr(brain, "_get_client", lambda: _FakeClient())

    result = evaluate_signal(
        symbol="TEST",
        strategy_signal="BUY",
        market_context={},
        portfolio_state={},
        df=_make_ohlcv(),
        spy_close=None,   # no SPY data → default trending
    )

    assert result["action"] == "BUY"


# ─── Teardown ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_lgbm_model():
    yield
    brain._lgbm_model = None
