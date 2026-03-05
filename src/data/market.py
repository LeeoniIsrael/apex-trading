"""Alpaca market data ingestion → DuckDB."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from src.config import settings

logger = logging.getLogger(__name__)


def get_client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )


def fetch_bars(
    symbols: list[str],
    start: datetime,
    end: datetime | None = None,
    timeframe: TimeFrame = TimeFrame.Day,
) -> list[dict]:
    client = get_client()
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        start=start,
        end=end or datetime.now(tz=timezone.utc),
        timeframe=timeframe,
        feed=DataFeed.IEX,   # free tier — use IEX (SIP requires paid subscription)
    )
    bars = client.get_stock_bars(request)
    rows = []
    for symbol, bar_list in bars.data.items():
        for bar in bar_list:
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": bar.timestamp,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "vwap": bar.vwap,
                }
            )
    return rows


def ingest_bars(
    conn: duckdb.DuckDBPyConnection,
    symbols: list[str],
    start: datetime,
    end: datetime | None = None,
    timeframe: TimeFrame = TimeFrame.Day,
) -> int:
    rows = fetch_bars(symbols, start, end, timeframe)
    if not rows:
        logger.warning("No bars returned for %s", symbols)
        return 0

    conn.executemany(
        """
        INSERT INTO bars (symbol, timestamp, open, high, low, close, volume, vwap)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, timestamp) DO NOTHING
        """,
        [
            (
                r["symbol"],
                r["timestamp"],
                r["open"],
                r["high"],
                r["low"],
                r["close"],
                r["volume"],
                r["vwap"],
            )
            for r in rows
        ],
    )
    logger.info("Ingested %d bars for %s", len(rows), symbols)
    return len(rows)
