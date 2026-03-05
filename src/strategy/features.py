"""Feature engineering and LightGBM signal layer."""

from __future__ import annotations

import logging
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)


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


def build_training_dataset(
    price_data: dict[str, pd.DataFrame],
    forward_days: int = 5,
) -> pd.DataFrame:
    """Build a labelled dataset from a dict of {symbol: OHLCV DataFrame}.

    Label: 1 if forward_days return > 0 (up), 0 otherwise.
    Returns a single DataFrame with all symbols concatenated.
    """
    frames = []
    for symbol, df in price_data.items():
        df = df.copy()
        # Normalise column names to lowercase
        df.columns = [c.lower() for c in df.columns]
        if not {"open", "high", "low", "close", "volume"}.issubset(df.columns):
            logger.warning("Skipping %s — missing OHLCV columns", symbol)
            continue
        df = add_momentum_features(df)
        df["symbol"] = symbol
        df["label"] = (df["close"].pct_change(forward_days).shift(-forward_days) > 0).astype(int)
        frames.append(df)

    if not frames:
        raise ValueError("No valid price data to build training dataset")

    combined = pd.concat(frames).dropna(subset=FEATURE_COLUMNS + ["label"])
    return combined


class LGBMSignalModel:
    """LightGBM binary classifier: predicts probability of positive 5-day forward return.

    Trained with time-series cross-validation (no look-ahead).
    """

    def __init__(self, n_splits: int = 5) -> None:
        self.n_splits = n_splits
        self.model: lgb.Booster | None = None
        self.scaler = StandardScaler()
        self.feature_importance_: pd.Series | None = None
        self.cv_scores_: list[float] = []

    def fit(self, dataset: pd.DataFrame) -> "LGBMSignalModel":
        """Train on a pre-built dataset (output of build_training_dataset)."""
        X = dataset[FEATURE_COLUMNS].values
        y = dataset["label"].values

        X_scaled = self.scaler.fit_transform(X)

        tscv = TimeSeriesSplit(n_splits=self.n_splits)
        params = {
            "objective": "binary",
            "metric": "auc",
            "learning_rate": 0.05,
            "num_leaves": 31,
            "min_child_samples": 50,
            "n_estimators": 200,
            "verbose": -1,
            "random_state": 42,
        }

        # CV to measure generalisation
        for fold, (train_idx, val_idx) in enumerate(tscv.split(X_scaled)):
            clf = lgb.LGBMClassifier(**params)
            clf.fit(
                X_scaled[train_idx], y[train_idx],
                eval_set=[(X_scaled[val_idx], y[val_idx])],
                callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(period=-1)],
            )
            auc = clf.best_score_["valid_0"]["auc"]
            self.cv_scores_.append(auc)
            logger.info("Fold %d AUC: %.4f", fold + 1, auc)

        # Final model on all data — use named DataFrame so predict_proba is consistent
        X_scaled_df = pd.DataFrame(X_scaled, columns=FEATURE_COLUMNS)
        final = lgb.LGBMClassifier(**params)
        final.fit(X_scaled_df, y)
        self.model = final

        self.feature_importance_ = pd.Series(
            final.feature_importances_,
            index=FEATURE_COLUMNS,
            name="importance",
        ).sort_values(ascending=False)

        logger.info("Mean CV AUC: %.4f ± %.4f", np.mean(self.cv_scores_), np.std(self.cv_scores_))
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        """Return probability of positive return for each row."""
        if self.model is None:
            raise RuntimeError("Model not trained — call fit() first")
        X = df[FEATURE_COLUMNS].values
        X_scaled = pd.DataFrame(self.scaler.transform(X), columns=FEATURE_COLUMNS)
        return self.model.predict_proba(X_scaled)[:, 1]

    def signal(self, df: pd.DataFrame, threshold: float = 0.55) -> pd.Series:
        """Return 1/0 signal series (1 = bullish) based on probability threshold."""
        proba = self.predict_proba(df)
        return pd.Series((proba >= threshold).astype(int), index=df.index, name="lgbm_signal")
