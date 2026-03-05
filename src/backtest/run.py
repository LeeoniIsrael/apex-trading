"""vectorbt backtest runner.

Usage:
    uv run python -m src.backtest.run --strategy momentum --symbols SPY QQQ AAPL MSFT NVDA
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import pandas as pd
import vectorbt as vbt

logger = logging.getLogger(__name__)


def run_momentum_backtest(
    symbols: list[str],
    start: str = "2020-01-01",
    end: str = "2024-12-31",
    lookback: int = 20,
    top_n: int = 5,
) -> vbt.Portfolio:
    """Cross-sectional momentum backtest using vectorbt."""
    logger.info("Downloading data for %s (%s → %s)", symbols, start, end)
    data = vbt.YFData.download(symbols, start=start, end=end, missing_index="drop", missing_columns="drop")
    close = data.get("Close")

    # Momentum score: return_20d / volatility_20d
    returns = close.pct_change(lookback)
    vol = close.pct_change().rolling(lookback).std()
    scores = returns / vol

    # Long top N ranked symbols each period
    ranks = scores.rank(axis=1, ascending=False)
    entries = ranks <= top_n
    exits = ranks > top_n

    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        size=1 / top_n,
        size_type="percent",
        fees=0.001,
        freq="1D",
        group_by=True,
        cash_sharing=True,
    )

    stats = pf.stats()
    logger.info("Sharpe ratio: %.3f", stats["Sharpe Ratio"])
    logger.info("Max drawdown: %.2f%%", stats["Max Drawdown [%]"])
    logger.info("Total return: %.2f%%", stats["Total Return [%]"])

    return pf


def run_mean_reversion_backtest(
    symbols: list[str],
    start: str = "2020-01-01",
    end: str = "2024-12-31",
    window: int = 20,
    num_std: float = 2.0,
) -> vbt.Portfolio:
    """Bollinger Band mean reversion backtest."""
    data = vbt.YFData.download(symbols, start=start, end=end, missing_index="drop", missing_columns="drop")
    close = data.get("Close")

    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    lower = mid - num_std * std
    upper = mid + num_std * std

    entries = close < lower
    exits = close > mid

    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        fees=0.001,
        freq="1D",
        group_by=True,
        cash_sharing=True,
    )

    stats = pf.stats()
    logger.info("Sharpe ratio: %.3f", stats["Sharpe Ratio"])
    logger.info("Max drawdown: %.2f%%", stats["Max Drawdown [%]"])
    return pf


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="APEX backtest runner")
    parser.add_argument(
        "--strategy",
        choices=["momentum", "mean_reversion"],
        default="momentum",
    )
    parser.add_argument("--symbols", nargs="+", default=[
        # Tech (8)
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
        # Finance (5)
        "JPM", "BAC", "GS", "BRK-B", "V",
        # Healthcare (4)
        "JNJ", "UNH", "LLY", "ABBV",
        # Consumer (4)
        "HD", "MCD", "NKE", "COST",
        # Energy (4)
        "XOM", "CVX", "COP", "SLB",
    ])
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2024-12-31")
    args = parser.parse_args()

    if args.strategy == "momentum":
        pf = run_momentum_backtest(args.symbols, args.start, args.end)
    else:
        pf = run_mean_reversion_backtest(args.symbols, args.start, args.end)

    print(pf.stats())


if __name__ == "__main__":
    main()
