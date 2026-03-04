"""DuckDB schema definitions for APEX."""

import duckdb


CREATE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS bars (
        symbol      VARCHAR NOT NULL,
        timestamp   TIMESTAMPTZ NOT NULL,
        open        DOUBLE NOT NULL,
        high        DOUBLE NOT NULL,
        low         DOUBLE NOT NULL,
        close       DOUBLE NOT NULL,
        volume      BIGINT NOT NULL,
        vwap        DOUBLE,
        PRIMARY KEY (symbol, timestamp)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id          INTEGER PRIMARY KEY,
        timestamp   TIMESTAMPTZ NOT NULL,
        symbol      VARCHAR NOT NULL,
        strategy    VARCHAR NOT NULL,
        signal      VARCHAR NOT NULL,    -- BUY | SELL | HOLD
        confidence  DOUBLE,
        features    JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id              VARCHAR PRIMARY KEY,  -- Alpaca order id
        timestamp       TIMESTAMPTZ NOT NULL,
        symbol          VARCHAR NOT NULL,
        side            VARCHAR NOT NULL,     -- buy | sell
        qty             DOUBLE NOT NULL,
        fill_price      DOUBLE,
        status          VARCHAR NOT NULL,
        strategy        VARCHAR,
        reasoning       TEXT,                 -- Claude's reasoning
        paper           BOOLEAN NOT NULL DEFAULT true
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        timestamp       TIMESTAMPTZ PRIMARY KEY,
        cash            DOUBLE NOT NULL,
        equity          DOUBLE NOT NULL,
        unrealized_pnl  DOUBLE NOT NULL,
        realized_pnl    DOUBLE NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_logs (
        id          INTEGER PRIMARY KEY,
        timestamp   TIMESTAMPTZ NOT NULL,
        level       VARCHAR NOT NULL,   -- INFO | WARN | ERROR | DECISION
        message     TEXT NOT NULL,
        metadata    JSON
    )
    """,
]

SEQUENCE_STATEMENTS = [
    "CREATE SEQUENCE IF NOT EXISTS seq_signals START 1",
    "CREATE SEQUENCE IF NOT EXISTS seq_agent_logs START 1",
]


def init_db(db_path: str = "data/apex.duckdb") -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(db_path)
    for stmt in SEQUENCE_STATEMENTS:
        conn.execute(stmt)
    for stmt in CREATE_STATEMENTS:
        conn.execute(stmt)
    return conn
