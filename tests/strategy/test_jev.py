from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.monitoring.costs import CostTracker
from src.storage.database import Database
from src.strategy.jev import JevBudgetExceeded, JevClient


class FakeResponse:
    headers = {"x-request-id": "one"}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "model": "jev-1.13.0",
            "answers": {
                "decision": {"choice": "TRADE", "confidence": 0.91},
                "opportunity_priority": {"choice": "HIGH", "confidence": 0.90},
                "data_state": {"choice": "CLEAN", "confidence": 0.92},
                "safe_to_trade": {"noul": 0.89},
            },
            "usage": {"cost_usd": 0.0001},
        }


class FakeSession:
    def __init__(self):
        self.request = None

    def post(self, url, **kwargs):
        self.request = (url, kwargs)
        return FakeResponse()


def _tracker(tmp_path: Path) -> CostTracker:
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    return CostTracker(database)


def test_jev_uses_typed_compact_veto_schema_and_records_cost(tmp_path: Path):
    tracker = _tracker(tmp_path)
    session = FakeSession()
    client = JevClient(
        api_key="secret", cost_tracker=tracker, daily_budget_usd=0.1,
        monthly_budget_usd=1.0, session=session,  # type: ignore[arg-type]
    )
    review = client.review({"ticker": "TEST", "net_ev_usd": 0.25})
    assert review.decision.decision == "TRADE"
    assert review.decision.confidence == 0.89
    assert review.decision.veto_codes(0.75) == ()
    assert tracker.total_since(
        "jev_api", datetime.min.replace(tzinfo=timezone.utc)
    ) == pytest.approx(0.0001)
    _, request = session.request
    assert request["json"]["questions"]["safe_to_trade"]["type"] == "noul"
    assert "secret" not in str(request["json"])


def test_jev_budget_exhaustion_fails_before_network(tmp_path: Path):
    tracker = _tracker(tmp_path)
    session = FakeSession()
    client = JevClient(
        api_key="secret", cost_tracker=tracker, daily_budget_usd=0,
        monthly_budget_usd=0, session=session,  # type: ignore[arg-type]
    )
    with pytest.raises(JevBudgetExceeded):
        client.review({"ticker": "TEST"})
    assert session.request is None
