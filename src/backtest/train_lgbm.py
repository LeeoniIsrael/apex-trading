"""Train the LightGBM signal model on the 25-symbol universe and display feature importance.

Usage:
    uv run python -m src.backtest.train_lgbm
"""

from __future__ import annotations

import logging

import vectorbt as vbt

from src.strategy.features import LGBMSignalModel, build_training_dataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

UNIVERSE = [
    # Tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    # Finance
    "JPM", "BAC", "GS", "BRK-B", "V",
    # Healthcare
    "JNJ", "UNH", "LLY", "ABBV",
    # Consumer
    "HD", "MCD", "NKE", "COST",
    # Energy
    "XOM", "CVX", "COP", "SLB",
]


def main() -> None:
    logger.info("Downloading price data for %d symbols…", len(UNIVERSE))
    data = vbt.YFData.download(
        UNIVERSE,
        start="2020-01-01",
        end="2024-12-31",
        missing_index="drop",
        missing_columns="drop",
    )

    # Build per-symbol OHLCV dicts with lowercase column names
    price_data: dict = {}
    for sym in UNIVERSE:
        try:
            df = data.get(sym) if hasattr(data, "get") else data.data[sym]
            price_data[sym] = df
        except Exception as exc:
            logger.warning("Skipping %s: %s", sym, exc)

    # Reshape: vectorbt returns wide DataFrames per field — rebuild per-symbol
    ohlcv_map: dict = {}
    for sym in UNIVERSE:
        try:
            sym_df = data.select(sym).get()
            sym_df.columns = [c.lower() for c in sym_df.columns]
            ohlcv_map[sym] = sym_df
        except Exception:
            pass

    if not ohlcv_map:
        # Fallback: reconstruct from field-level DataFrames
        logger.info("Reconstructing per-symbol DataFrames from field arrays…")
        fields = ["Open", "High", "Low", "Close", "Volume"]
        raw = {f: data.get(f) for f in fields}
        for sym in UNIVERSE:
            try:
                sym_df = {f.lower(): raw[f][sym] for f in fields}
                import pandas as pd
                ohlcv_map[sym] = pd.DataFrame(sym_df)
            except Exception as exc:
                logger.warning("Could not reconstruct %s: %s", sym, exc)

    logger.info("Building training dataset…")
    dataset = build_training_dataset(ohlcv_map)
    logger.info("Dataset: %d rows, label balance: %.1f%% positive",
                len(dataset), dataset["label"].mean() * 100)

    logger.info("Training LightGBM model…")
    model = LGBMSignalModel(n_splits=5)
    model.fit(dataset)

    print("\n" + "=" * 50)
    print(f"Mean CV AUC: {sum(model.cv_scores_) / len(model.cv_scores_):.4f}")
    print(f"CV AUC by fold: {[f'{s:.4f}' for s in model.cv_scores_]}")
    print("\nFeature Importance (gain):")
    print("-" * 35)
    total = model.feature_importance_.sum()
    for feat, imp in model.feature_importance_.items():
        bar = "#" * int(imp / total * 30)
        print(f"  {feat:<20} {imp:>6.0f}  {bar}")
    print("=" * 50)


if __name__ == "__main__":
    main()
