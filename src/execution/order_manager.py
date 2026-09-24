"""Order lifecycle maintenance shared by unattended paper operation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.storage.database import Database


def cancel_stale_paper_orders(database: Database, max_age: timedelta = timedelta(minutes=5)) -> int:
    cutoff = (datetime.now(timezone.utc) - max_age).isoformat()
    with database.transaction() as connection:
        cursor = connection.execute(
            "UPDATE orders SET status='cancelled',updated_at=? WHERE paper=1 "
            "AND status IN ('resting','partially_filled') AND created_at < ?",
            (datetime.now(timezone.utc).isoformat(), cutoff),
        )
        return cursor.rowcount
