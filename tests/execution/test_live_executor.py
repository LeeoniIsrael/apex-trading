from pathlib import Path

import pytest

from src.execution.live_executor import LiveExecutor
from src.storage.database import Database
from src.weather_config import WeatherSettings


class FakeClient:
    def __init__(self):
        self.calls = []

    def create_order(self, **kwargs):
        self.calls.append(kwargs)
        return {"order": {"status": "resting", "fill_count": 0}}

    def get_orders(self):
        return {"orders": []}

    def get_positions(self):
        return {"market_positions": []}


class UntrackedPositionClient(FakeClient):
    def get_positions(self):
        return {"market_positions": [{
            "ticker": "UNKNOWN", "position": 3,
            "market_exposure_dollars": "1.23", "realized_pnl_dollars": "0.00",
        }]}


def test_live_executor_requires_persisted_enablement(tmp_path: Path):
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    settings = WeatherSettings(
        trading_mode="paper", database_path=database.path,
        live_enablement_path=tmp_path / "missing",
    )
    with pytest.raises(RuntimeError, match="not explicitly enabled"):
        LiveExecutor(settings, FakeClient(), database)  # type: ignore[arg-type]


def test_live_order_is_idempotently_recorded(tmp_path: Path):
    enablement = tmp_path / "LIVE_ENABLED"
    enablement.write_text("enabled")
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    settings = WeatherSettings(
        trading_mode="live", database_path=database.path,
        live_enablement_path=enablement,
    )
    client = FakeClient()
    executor = LiveExecutor(settings, client, database)  # type: ignore[arg-type]
    executor.submit_buy(
        ticker="TEST", side="yes", price_cents=40, contracts=2,
        client_order_id="stable-id",
    )
    with pytest.raises(RuntimeError, match="duplicate client order"):
        executor.submit_buy(
            ticker="TEST", side="yes", price_cents=40, contracts=2,
            client_order_id="stable-id",
        )
    assert len(client.calls) == 1


def test_live_reconciliation_blocks_unknown_remote_cost_basis(tmp_path: Path):
    enablement = tmp_path / "LIVE_ENABLED"
    enablement.write_text("enabled")
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    settings = WeatherSettings(
        trading_mode="live", database_path=database.path,
        live_enablement_path=enablement,
    )
    executor = LiveExecutor(
        settings, UntrackedPositionClient(), database,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="manual cost-basis reconciliation"):
        executor.reconcile()
    with database.connect() as connection:
        position = connection.execute(
            "SELECT contracts,average_price_cents FROM positions WHERE ticker='UNKNOWN'"
        ).fetchone()
    assert tuple(position) == (3, 0.0)
