"""Combined backtest: momentum vs momentum + LightGBM confirmation filter.

Methodology:
- Training window:    2020-01-01 → 2022-12-31  (LightGBM fit in-sample)
- Test window:        2023-01-01 → 2024-12-31  (out-of-sample comparison)
- Momentum signal:    top-5 by 20-day risk-adjusted return (same as run.py)
- Filter condition:   LightGBM proba >= 0.55 required to open a position

Prints a side-by-side stats table for both strategies.

Usage:
    uv run python -m src.backtest.combined
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import vectorbt as vbt

from src.strategy.features import (
    FEATURE_COLUMNS,
    LGBMSignalModel,
    add_momentum_features,
    build_training_dataset,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "JPM", "BAC", "GS", "BRK-B", "V",
    "JNJ", "UNH", "LLY", "ABBV",
    "HD", "MCD", "NKE", "COST",
    "XOM", "CVX", "COP", "SLB",
]

TRAIN_START = "2020-01-01"
TRAIN_END   = "2022-12-31"
TEST_START  = "2023-01-01"
TEST_END    = "2024-12-31"
LOOKBACK    = 20
TOP_N       = 5
LGBM_THRESHOLD = 0.55


def _download(start: str, end: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Return (close_wide, ohlcv_map) for the universe."""
    data = vbt.YFData.download(
        UNIVERSE, start=start, end=end,
        missing_index="drop", missing_columns="drop",
    )
    close = data.get("Close")

    fields = ["Open", "High", "Low", "Close", "Volume"]
    raw = {f: data.get(f) for f in fields}
    ohlcv_map: dict[str, pd.DataFrame] = {}
    for sym in UNIVERSE:
        try:
            ohlcv_map[sym] = pd.DataFrame({f.lower(): raw[f][sym] for f in fields})
        except Exception:
            pass
    return close, ohlcv_map


def _momentum_entries_exits(
    close: pd.DataFrame,
    lookback: int = LOOKBACK,
    top_n: int = TOP_N,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    returns = close.pct_change(lookback)
    vol     = close.pct_change().rolling(lookback).std()
    scores  = returns / vol
    ranks   = scores.rank(axis=1, ascending=False)
    return ranks <= top_n, ranks > top_n


def _build_lgbm_proba_matrix(
    model: LGBMSignalModel,
    ohlcv_map: dict[str, pd.DataFrame],
    index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Build a (date × symbol) DataFrame of LightGBM BUY probabilities."""
    rows: dict[str, pd.Series] = {}
    for sym, df in ohlcv_map.items():
        df = df.copy()
        df = add_momentum_features(df)
        valid = df.dropna(subset=FEATURE_COLUMNS)
        if valid.empty:
            rows[sym] = pd.Series(np.nan, index=index)
            continue
        proba = model.predict_proba(valid)
        s = pd.Series(proba, index=valid.index)
        rows[sym] = s.reindex(index)

    return pd.DataFrame(rows, index=index)


def run_combined_backtest() -> None:
    # ------------------------------------------------------------------ #
    # 1. Download training data and fit LightGBM                          #
    # ------------------------------------------------------------------ #
    logger.info("Downloading training data (%s → %s)…", TRAIN_START, TRAIN_END)
    _, train_ohlcv = _download(TRAIN_START, TRAIN_END)

    logger.info("Building training dataset and fitting LightGBM…")
    train_ds = build_training_dataset(train_ohlcv)
    model = LGBMSignalModel(n_splits=5)
    model.fit(train_ds)
    logger.info(
        "LightGBM fit complete. Mean CV AUC: %.4f",
        np.mean(model.cv_scores_),
    )

    # ------------------------------------------------------------------ #
    # 2. Download test data                                                #
    # ------------------------------------------------------------------ #
    logger.info("Downloading test data (%s → %s)…", TEST_START, TEST_END)
    test_close, test_ohlcv = _download(TEST_START, TEST_END)

    # ------------------------------------------------------------------ #
    # 3. Pure momentum signals on test window                              #
    # ------------------------------------------------------------------ #
    mom_entries, mom_exits = _momentum_entries_exits(test_close)

    # ------------------------------------------------------------------ #
    # 4. LightGBM probability matrix on test window                        #
    # ------------------------------------------------------------------ #
    logger.info("Computing LightGBM probabilities on test window…")
    proba_matrix = _build_lgbm_proba_matrix(model, test_ohlcv, test_close.index)

    # Filter: entry allowed only when momentum AND lgbm_proba >= threshold
    lgbm_mask    = proba_matrix >= LGBM_THRESHOLD
    filt_entries = mom_entries & lgbm_mask.fillna(False)
    filt_exits   = mom_exits   # exits unchanged — never block sells

    # ------------------------------------------------------------------ #
    # 5. Run both portfolios                                               #
    # ------------------------------------------------------------------ #
    shared_kwargs = dict(
        size=1 / TOP_N,
        size_type="percent",
        fees=0.001,
        freq="1D",
        group_by=True,
        cash_sharing=True,
    )

    pf_momentum = vbt.Portfolio.from_signals(
        test_close, entries=mom_entries,  exits=mom_exits,  **shared_kwargs
    )
    pf_filtered = vbt.Portfolio.from_signals(
        test_close, entries=filt_entries, exits=filt_exits, **shared_kwargs
    )

    # ------------------------------------------------------------------ #
    # 6. Print comparison                                                   #
    # ------------------------------------------------------------------ #
    s_mom  = pf_momentum.stats()
    s_filt = pf_filtered.stats()

    metrics = [
        "Total Return [%]",
        "Sharpe Ratio",
        "Sortino Ratio",
        "Calmar Ratio",
        "Max Drawdown [%]",
        "Max Drawdown Duration",
        "Win Rate [%]",
        "Total Trades",
        "Profit Factor",
    ]

    print()
    print("=" * 65)
    print(f"  COMBINED BACKTEST — Out-of-sample: {TEST_START} → {TEST_END}")
    print(f"  LightGBM trained on:               {TRAIN_START} → {TRAIN_END}")
    print(f"  Universe: {len(UNIVERSE)} symbols | top_n={TOP_N} | threshold={LGBM_THRESHOLD}")
    print("=" * 65)
    print(f"  {'Metric':<28} {'Momentum':>14} {'Mom+LGBM':>14}")
    print("  " + "-" * 61)
    for m in metrics:
        v_mom  = s_mom.get(m,  "N/A")
        v_filt = s_filt.get(m, "N/A")
        if isinstance(v_mom,  float): v_mom  = f"{v_mom:.3f}"
        if isinstance(v_filt, float): v_filt = f"{v_filt:.3f}"
        print(f"  {m:<28} {str(v_mom):>14} {str(v_filt):>14}")
    print("=" * 65)

    # Highlight the Sharpe delta
    delta = s_filt["Sharpe Ratio"] - s_mom["Sharpe Ratio"]
    direction = "improved" if delta >= 0 else "hurt"
    print(f"\n  Sharpe delta (filtered − base): {delta:+.4f}  [{direction}]")
    print()

    return pf_momentum, pf_filtered, model


if __name__ == "__main__":
    run_combined_backtest()
