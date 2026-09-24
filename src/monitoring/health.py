"""Local safety checks suitable for a systemd watchdog command."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.storage.database import Database


@dataclass(frozen=True, slots=True)
class HealthReport:
    healthy: bool
    checks: dict[str, str]


def check_health(database: Database, data_directory: Path, min_free_mb: int = 512) -> HealthReport:
    checks: dict[str, str] = {}
    checks["database_integrity"] = database.integrity_check()
    free_mb = shutil.disk_usage(data_directory).free // (1024 * 1024)
    checks["disk_free_mb"] = str(free_mb)
    # Detect gross wall-clock corruption without requiring external NTP access.
    now = datetime.now(timezone.utc)
    checks["clock_unix"] = str(int(now.timestamp()))
    checks["monotonic"] = str(round(time.monotonic(), 3))
    with database.connect() as connection:
        heartbeat = connection.execute(
            "SELECT metric_date FROM strategy_metrics WHERE metric_name='service_heartbeat' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    heartbeat_fresh = True
    if heartbeat:
        heartbeat_at = datetime.fromisoformat(str(heartbeat[0]).replace("Z", "+00:00"))
        heartbeat_age = now - heartbeat_at.astimezone(timezone.utc)
        checks["heartbeat_age_seconds"] = str(round(heartbeat_age.total_seconds()))
        heartbeat_fresh = heartbeat_age <= timedelta(minutes=5)
    else:
        checks["heartbeat_age_seconds"] = "not_recorded"
    healthy = (
        checks["database_integrity"] == "ok"
        and free_mb >= min_free_mb
        and heartbeat_fresh
    )
    return HealthReport(healthy, checks)
