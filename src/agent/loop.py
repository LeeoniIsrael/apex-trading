"""APEX autonomous trading loop.

Schedule:
  - 09:31 ET: Morning signal scan + execute orders
  - 15:45 ET: Afternoon rebalance check
  - 16:05 ET: End-of-day analysis + journal entry

Start:  April 1, 2026 at midnight ET
Stop:   May 1, 2026 at midnight ET
"""

from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# US/Eastern — APScheduler uses local or explicit timezone
MARKET_TZ = "America/New_York"

EXPERIMENT_START = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
EXPERIMENT_END = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)


def morning_scan() -> None:
    now = datetime.now(tz=timezone.utc)
    if now < EXPERIMENT_START or now >= EXPERIMENT_END:
        logger.info("Outside experiment window — skipping morning scan")
        return
    logger.info("=== APEX Morning Scan %s ===", now.date())
    # TODO: fetch latest bars → generate signals → evaluate with brain → execute


def afternoon_rebalance() -> None:
    now = datetime.now(tz=timezone.utc)
    if now < EXPERIMENT_START or now >= EXPERIMENT_END:
        return
    logger.info("=== APEX Afternoon Rebalance %s ===", now.date())
    # TODO: check positions against current signals → trim if needed


def end_of_day() -> None:
    now = datetime.now(tz=timezone.utc)
    if now < EXPERIMENT_START or now >= EXPERIMENT_END:
        return
    logger.info("=== APEX End-of-Day %s ===", now.date())
    # TODO: fetch today's trades → portfolio snapshot → brain.end_of_day_analysis → write journal


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=MARKET_TZ)

    # Weekdays only (Mon–Fri)
    scheduler.add_job(
        morning_scan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=31, timezone=MARKET_TZ),
        id="morning_scan",
        name="Morning signal scan",
    )
    scheduler.add_job(
        afternoon_rebalance,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=MARKET_TZ),
        id="afternoon_rebalance",
        name="Afternoon rebalance",
    )
    scheduler.add_job(
        end_of_day,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone=MARKET_TZ),
        id="end_of_day",
        name="End-of-day analysis",
    )
    return scheduler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    scheduler = build_scheduler()

    def _shutdown(signum, frame):
        logger.info("Shutting down APEX agent…")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("APEX agent starting. Experiment window: %s → %s", EXPERIMENT_START.date(), EXPERIMENT_END.date())
    scheduler.start()


if __name__ == "__main__":
    main()
