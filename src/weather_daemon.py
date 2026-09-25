"""Long-running weather service with graceful shutdown and adaptive polling."""

from __future__ import annotations

import json
import logging
import signal
import threading
from datetime import datetime, timezone
import time

from src.weather_config import WeatherSettings
from src.weather_service import WeatherService

logger = logging.getLogger(__name__)


def stop_after_live_cycle_failure(service) -> None:
    """Persist a live pause and an alert; never retry an uncertain trade blindly."""
    if service.live is None:
        return
    with service.live.database.transaction() as connection:
        stamp = datetime.now(timezone.utc).isoformat()
        connection.execute('INSERT OR REPLACE INTO control_state VALUES(?,?,?)',
                           ('paused', 'true', stamp))
        connection.execute(
            'INSERT INTO health_events(occurred_at,severity,component,code,message,details_json) '
            'VALUES(?,?,?,?,?,?)',
            (stamp, 'critical', 'live', 'cycle_failed',
             'Trading paused after a failed cycle; operator reconciliation required', '{}'))


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def main() -> int:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("shutdown requested by signal %s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    service = WeatherService(WeatherSettings())
    last_launch_report = 0.0
    while not stop.is_set():
        try:
            result = service.run_once()
            logger.info("cycle complete %s", result)
            with service.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO strategy_metrics(metric_date,station_id,metric_name,"
                    "metric_value,dimensions_json) VALUES(?,NULL,'service_heartbeat',1,?)",
                    (datetime.now(timezone.utc).isoformat(), json.dumps(result, sort_keys=True)),
                )
            if time.monotonic()-last_launch_report >= 600:
                from src.research.launch_report import write_launch_report
                write_launch_report(service.database, service.settings.bankroll,
                                    service.settings.live_validation_profile)
                last_launch_report = time.monotonic()
        except Exception:
            stop_after_live_cycle_failure(service)
            logger.exception("weather cycle failed")
        stop.wait(service.recommended_poll_seconds())
    logger.info("weather service stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
