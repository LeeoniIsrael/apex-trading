"""Tests for the brain LightGBM gate logic."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import src.agent.brain as brain
from src.agent.brain import LGBM_THRESHOLD, apply_lgbm_gate, evaluate_signal
from src.strategy.features import LGBMSignalModel


def _make_ohlcv(n: int = 120, seed: int = 0) -> pd.DataFrame:
    np.random.seed(seed)
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


# ---------------------------------------------------------------------------
# apply_lgbm_gate — no model loaded (open by default)
# ---------------------------------------------------------------------------

def test_gate_open_when_model_not_initialised():
    brain._lgbm_model = None
    df = _make_ohlcv()
    proba, passes = apply_lgbm_gate("TEST", df)
    assert passes is True
    assert proba == 1.0


# ---------------------------------------------------------------------------
# apply_lgbm_gate — with a trained stub model
# ---------------------------------------------------------------------------

class _HighProbaModel:
    """Stub that always returns proba above threshold."""
    cv_scores_ = [0.55]
    feature_importance_ = None

    def predict_proba(self, df):
        # Match LGBMSignalModel.predict_proba return shape: 1D array of positive-class probas
        return np.full(len(df), 0.9)


class _LowProbaModel:
    """Stub that always returns proba below threshold."""
    cv_scores_ = [0.50]
    feature_importance_ = None

    def predict_proba(self, df):
        return np.full(len(df), 0.4)


def test_gate_passes_when_proba_above_threshold():
    brain._lgbm_model = _HighProbaModel()
    df = _make_ohlcv()
    proba, passes = apply_lgbm_gate("TEST", df)
    assert passes is True
    assert proba >= LGBM_THRESHOLD


def test_gate_blocks_when_proba_below_threshold():
    brain._lgbm_model = _LowProbaModel()
    df = _make_ohlcv()
    proba, passes = apply_lgbm_gate("TEST", df)
    assert passes is False
    assert proba < LGBM_THRESHOLD


# ---------------------------------------------------------------------------
# evaluate_signal — LightGBM gate blocks BUY before Claude is called
# ---------------------------------------------------------------------------

def test_evaluate_signal_hold_when_lgbm_blocks(monkeypatch):
    brain._lgbm_model = _LowProbaModel()

    # Ensure Claude is never called
    def _no_claude(*args, **kwargs):
        raise AssertionError("Claude should not be called when LGBM blocks")

    monkeypatch.setattr(brain, "_get_client", _no_claude)

    result = evaluate_signal(
        symbol="TEST",
        strategy_signal="BUY",
        market_context={"price": 100},
        portfolio_state={"cash": 10000},
        df=_make_ohlcv(),
    )

    assert result["action"] == "HOLD"
    assert "lgbm_confirmation_failed" in result["risk_factors"]


def test_evaluate_signal_sell_skips_lgbm_gate(monkeypatch):
    """SELL signals must never be blocked by LightGBM."""
    brain._lgbm_model = _LowProbaModel()

    # Track whether gate was checked by patching apply_lgbm_gate
    calls = []
    original = brain.apply_lgbm_gate

    def _tracked(symbol, df):
        calls.append(symbol)
        return original(symbol, df)

    monkeypatch.setattr(brain, "apply_lgbm_gate", _tracked)

    # Patch Claude to return a SELL without network call
    class _FakeMsg:
        class _Content:
            text = '{"action":"SELL","confidence":0.9,"reasoning":"test","risk_factors":[]}'
        content = [_Content()]

    class _FakeClient:
        def messages(self):
            pass
        class messages:
            @staticmethod
            def create(**kwargs):
                return _FakeMsg()

    monkeypatch.setattr(brain, "_get_client", lambda: _FakeClient())

    evaluate_signal(
        symbol="TEST",
        strategy_signal="SELL",
        market_context={},
        portfolio_state={},
        df=_make_ohlcv(),
    )

    assert calls == [], "apply_lgbm_gate must not be called for SELL signals"


# ---------------------------------------------------------------------------
# Teardown — reset global model state after tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_lgbm_model():
    yield
    brain._lgbm_model = None
