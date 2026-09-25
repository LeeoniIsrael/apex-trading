"""SQLite WAL database with explicit, repeatable migrations."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS markets (
        ticker TEXT PRIMARY KEY, event_ticker TEXT, series_ticker TEXT,
        status TEXT, raw_json TEXT NOT NULL, observed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS market_rules (
        id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, rules_text TEXT NOT NULL,
        rules_hash TEXT NOT NULL, retrieved_at TEXT NOT NULL,
        UNIQUE(ticker, rules_hash)
    );
    CREATE TABLE IF NOT EXISTS settlement_specs (
        ticker TEXT PRIMARY KEY, spec_json TEXT NOT NULL, parse_confidence REAL NOT NULL,
        tradeable INTEGER NOT NULL, ambiguity_flags TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS weather_stations (
        station_id TEXT PRIMARY KEY, climate_product_id TEXT, city TEXT NOT NULL,
        latitude REAL NOT NULL, longitude REAL NOT NULL, timezone TEXT NOT NULL,
        standard_utc_offset_hours INTEGER NOT NULL, source_note TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS weather_observations (
        id INTEGER PRIMARY KEY, station_id TEXT NOT NULL, source TEXT NOT NULL,
        report_type TEXT NOT NULL, observed_at_utc TEXT NOT NULL,
        observed_at_local TEXT NOT NULL, ingested_at_utc TEXT NOT NULL,
        raw_payload TEXT NOT NULL, temperature_f REAL, max_temperature_f REAL,
        min_temperature_f REAL, status TEXT NOT NULL,
        UNIQUE(station_id, source, report_type, observed_at_utc, raw_payload)
    );
    CREATE TABLE IF NOT EXISTS forecast_runs (
        id INTEGER PRIMARY KEY, station_id TEXT NOT NULL, provider TEXT NOT NULL,
        model TEXT NOT NULL, generated_at_utc TEXT NOT NULL,
        ingested_at_utc TEXT NOT NULL, raw_json TEXT NOT NULL,
        UNIQUE(station_id, provider, model, generated_at_utc)
    );
    CREATE TABLE IF NOT EXISTS weather_forecasts (
        id INTEGER PRIMARY KEY, forecast_run_id INTEGER NOT NULL,
        member INTEGER NOT NULL, valid_at_utc TEXT NOT NULL, temperature_f REAL NOT NULL,
        features_json TEXT NOT NULL, FOREIGN KEY(forecast_run_id) REFERENCES forecast_runs(id)
    );
    CREATE TABLE IF NOT EXISTS market_snapshots (
        id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, captured_at TEXT NOT NULL,
        yes_bid_cents INTEGER, yes_ask_cents INTEGER, no_bid_cents INTEGER,
        no_ask_cents INTEGER, volume REAL, raw_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, captured_at TEXT NOT NULL,
        orderbook_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS model_predictions (
        id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, predicted_at TEXT NOT NULL,
        model_version TEXT NOT NULL, probability REAL NOT NULL,
        confidence_low REAL, confidence_high REAL, inputs_json TEXT NOT NULL,
        high_so_far_f REAL, eventual_outcome INTEGER
    );
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, decided_at TEXT NOT NULL,
        action TEXT NOT NULL, reason_codes TEXT NOT NULL, edge_json TEXT
    );
    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE, ticker TEXT NOT NULL,
        side TEXT NOT NULL, action TEXT NOT NULL, price_cents INTEGER NOT NULL,
        contracts INTEGER NOT NULL, filled_contracts INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL, paper INTEGER NOT NULL, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS fills (
        id TEXT PRIMARY KEY, order_id TEXT NOT NULL, ticker TEXT NOT NULL,
        side TEXT NOT NULL, contracts INTEGER NOT NULL, price_cents INTEGER NOT NULL,
        fee_usd REAL NOT NULL, filled_at TEXT NOT NULL,
        FOREIGN KEY(order_id) REFERENCES orders(id)
    );
    CREATE TABLE IF NOT EXISTS positions (
        ticker TEXT NOT NULL, side TEXT NOT NULL, contracts INTEGER NOT NULL,
        average_price_cents REAL NOT NULL, realized_pnl_usd REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, PRIMARY KEY(ticker, side)
    );
    CREATE TABLE IF NOT EXISTS settlements (
        ticker TEXT PRIMARY KEY, official_value REAL, yes_outcome INTEGER,
        source TEXT NOT NULL, final INTEGER NOT NULL, settled_at TEXT NOT NULL,
        raw_payload TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS strategy_metrics (
        id INTEGER PRIMARY KEY, metric_date TEXT NOT NULL, station_id TEXT,
        metric_name TEXT NOT NULL, metric_value REAL NOT NULL, dimensions_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS costs (
        id INTEGER PRIMARY KEY, incurred_at TEXT NOT NULL, category TEXT NOT NULL,
        amount_usd REAL NOT NULL, description TEXT NOT NULL, external_id TEXT UNIQUE
    );
    CREATE TABLE IF NOT EXISTS health_events (
        id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, severity TEXT NOT NULL,
        component TEXT NOT NULL, code TEXT NOT NULL, message TEXT NOT NULL,
        details_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_observations_station_time
        ON weather_observations(station_id, observed_at_utc);
    CREATE INDEX IF NOT EXISTS idx_predictions_ticker_time
        ON model_predictions(ticker, predicted_at);
    CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_time
        ON market_snapshots(ticker, captured_at);
    """),
    (2, """
    CREATE TABLE IF NOT EXISTS control_state (
        key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    INSERT OR IGNORE INTO control_state(key,value,updated_at)
        VALUES('paused','false',CURRENT_TIMESTAMP);
    INSERT OR IGNORE INTO control_state(key,value,updated_at)
        VALUES('emergency_stop','false',CURRENT_TIMESTAMP);
    """),
    (3, """
    CREATE TABLE IF NOT EXISTS decision_benchmarks (
        id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, decided_at TEXT NOT NULL,
        deterministic_action TEXT NOT NULL, jev_action TEXT,
        final_action TEXT NOT NULL, latency_ms REAL NOT NULL DEFAULT 0,
        api_cost_usd REAL NOT NULL DEFAULT 0, entry_price_cents REAL,
        net_ev_usd REAL, fill_probability REAL, details_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_benchmarks_ticker_time
        ON decision_benchmarks(ticker, decided_at);
    """),
    (4, """
    CREATE TABLE IF NOT EXISTS research_candidates (
        id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, event_key TEXT NOT NULL,
        captured_at TEXT NOT NULL, split TEXT NOT NULL, model_version TEXT NOT NULL,
        side TEXT NOT NULL, price_cents REAL NOT NULL, contracts INTEGER NOT NULL,
        fee_usd REAL NOT NULL, probability REAL NOT NULL, net_ev_usd REAL NOT NULL,
        baseline_action TEXT NOT NULL, final_action TEXT NOT NULL, jev_action TEXT,
        source TEXT NOT NULL, station TEXT NOT NULL, city TEXT NOT NULL,
        market_type TEXT NOT NULL, price_bucket TEXT NOT NULL, time_bucket TEXT NOT NULL,
        seconds_to_close REAL NOT NULL, observation_age REAL NOT NULL,
        surprise_f REAL, forecast_disagreement_f REAL, spread_cents REAL,
        liquidity INTEGER NOT NULL, probability_change REAL, lag_candidate INTEGER NOT NULL,
        order_id TEXT, min_bid_cents REAL, control INTEGER NOT NULL,
        UNIQUE(ticker,captured_at)
    );
    CREATE TABLE IF NOT EXISTS equity_history (
        captured_at TEXT PRIMARY KEY, equity REAL NOT NULL, drawdown REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS verification_checks (
        name TEXT PRIMARY KEY, passed INTEGER NOT NULL, checked_at TEXT NOT NULL,
        details TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS notification_deliveries (
        event_key TEXT PRIMARY KEY, sent_at TEXT NOT NULL
    );
    """),
    (5, """
    CREATE INDEX IF NOT EXISTS idx_orderbooks_ticker_id ON orderbook_snapshots(ticker,id);
    CREATE INDEX IF NOT EXISTS idx_candidates_event_id ON research_candidates(event_key,id);
    CREATE INDEX IF NOT EXISTS idx_candidates_ticker_id ON research_candidates(ticker,id);
    """),
)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                connection.executescript(sql)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))

    def integrity_check(self) -> str:
        with self.connect() as connection:
            return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
