"""Tests for DuckDB schema initialization."""

import pytest
import duckdb

from src.data.schema import init_db


@pytest.fixture
def in_memory_db():
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


def test_init_db_creates_all_tables(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    conn = init_db(db_path)

    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    table_names = {row[0] for row in tables}

    expected = {"bars", "signals", "trades", "portfolio_snapshots", "agent_logs"}
    assert expected.issubset(table_names)
    conn.close()


def test_init_db_idempotent(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    conn1 = init_db(db_path)
    conn1.close()
    # Should not raise on second call
    conn2 = init_db(db_path)
    conn2.close()
