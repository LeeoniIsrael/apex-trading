import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.monitoring.health import check_health
from src.storage.database import Database
from src.weather_daemon import JSONFormatter


def test_daemon_formatter_emits_valid_json():
    record = logging.LogRecord(
        "weather", logging.INFO, __file__, 1, 'cycle "ok"', (), None,
    )
    payload = json.loads(JSONFormatter().format(record))
    assert payload["message"] == 'cycle "ok"'
    assert payload["level"] == "INFO"


def test_stale_existing_heartbeat_fails_health(tmp_path: Path):
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    stale = datetime.now(timezone.utc) - timedelta(minutes=6)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO strategy_metrics(metric_date,station_id,metric_name," 
            "metric_value,dimensions_json) VALUES(?,NULL,'service_heartbeat',1,'{}')",
            (stale.isoformat(),),
        )
    report = check_health(database, tmp_path, min_free_mb=0)
    assert not report.healthy
