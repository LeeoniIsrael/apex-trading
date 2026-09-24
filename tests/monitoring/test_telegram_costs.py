from pathlib import Path
from datetime import datetime, timezone

from src.monitoring.costs import CostTracker
from src.monitoring.telegram import TelegramController
from src.storage.database import Database


def test_cost_tracker_and_control_commands(tmp_path: Path):
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    tracker = CostTracker(database)
    tracker.record("infrastructure", 21.09, "CPX11 + IPv4", "hetzner-2026-09")
    tracker.record("infrastructure", 21.09, "duplicate", "hetzner-2026-09")
    assert tracker.total("infrastructure") == 21.09
    report = tracker.report(datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ))
    assert report.infrastructure_usd > 0

    controller = TelegramController(database)
    assert "paused" in controller.handle("/pause").lower()
    assert "resumed" in controller.handle("/resume").lower()
    assert "EMERGENCY STOP" in controller.handle("/emergency_stop")
    assert "latched" in controller.handle("/resume")
    assert "gross" in controller.handle("/today")
    assert "7d" in controller.handle("/pnl")
    assert "infrastructure" in controller.handle("/costs")
