from pathlib import Path
from types import SimpleNamespace

import pytest

from src.monitoring.costs import CostTracker
from src.storage.database import Database
from src.strategy.exception_review import ExceptionReview
from src.strategy.runtime_review import RuntimeExceptionReviewer, RuntimeReviewBudgetExceeded


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            id="response-one",
            output_parsed=ExceptionReview(
                allow=False, settlement_interpretation_confidence=0.8,
                data_consistent=False, anomaly=True,
                reason_codes=["forecast_disagreement"],
            ),
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


class FakeOpenAI:
    def __init__(self):
        self.responses = FakeResponses()


def _tracker(tmp_path: Path) -> CostTracker:
    database = Database(tmp_path / "db.sqlite3")
    database.migrate()
    return CostTracker(database)


def test_runtime_reviewer_uses_structured_schema_and_can_only_veto(tmp_path: Path):
    tracker = _tracker(tmp_path)
    fake = FakeOpenAI()
    reviewer = RuntimeExceptionReviewer(
        api_key="secret", cost_tracker=tracker, daily_budget_usd=.1,
        monthly_budget_usd=1, client=fake,  # type: ignore[arg-type]
    )
    result = reviewer.review({"ticker": "TEST", "conflict": 8.2})
    assert result.review.veto_codes == ("forecast_disagreement",)
    assert fake.responses.kwargs["text_format"] is ExceptionReview
    assert fake.responses.kwargs["model"] == "gpt-6-luna"
    assert "secret" not in str(fake.responses.kwargs["input"])
    assert tracker.total("openai_api") == 0.0  # rounded display; raw cost is retained


def test_runtime_reviewer_budget_fails_before_api_call(tmp_path: Path):
    fake = FakeOpenAI()
    reviewer = RuntimeExceptionReviewer(
        api_key="secret", cost_tracker=_tracker(tmp_path), daily_budget_usd=0,
        monthly_budget_usd=0, client=fake,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeReviewBudgetExceeded):
        reviewer.review({"ticker": "TEST"})
    assert fake.responses.kwargs is None


def test_runtime_timeout_retains_budget_reservation(tmp_path):
    tracker=_tracker(tmp_path)
    fake=FakeOpenAI()
    def timeout(**kwargs): raise TimeoutError()
    fake.responses.parse=timeout
    reviewer=RuntimeExceptionReviewer(api_key='unused',cost_tracker=tracker,
        daily_budget_usd=.1,monthly_budget_usd=1,client=fake)
    with pytest.raises(TimeoutError): reviewer.review({'conflict':True})
    with tracker.database.connect() as c:
        assert c.execute("SELECT SUM(amount_usd) FROM costs WHERE category='openai_api'").fetchone()[0]>0
