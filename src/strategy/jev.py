"""Optional, budgeted Jev veto layer for bounded trade decisions."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from src.monitoring.costs import CostTracker
from src.strategy.exception_review import JevDecision


class JevBudgetExceeded(RuntimeError):
    """Raised before a request when its estimated cost would exceed a cap."""


@dataclass(frozen=True, slots=True)
class JevReview:
    decision: JevDecision
    latency_ms: float
    cost_usd: float
    model: str


class JevClient:
    """Call Jev with a fixed typed schema; never ask it to produce a probability."""

    def __init__(
        self,
        *,
        api_key: str,
        cost_tracker: CostTracker,
        daily_budget_usd: float,
        monthly_budget_usd: float,
        endpoint: str = "https://jevtypesafeai.com/api/v1/decide",
        model: str = "jev-latest",
        timeout_seconds: float = 2.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("JEV_API_KEY is required when Jev is enabled")
        self.api_key = api_key
        self.cost_tracker = cost_tracker
        self.daily_budget_usd = daily_budget_usd
        self.monthly_budget_usd = monthly_budget_usd
        self.endpoint = endpoint
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    @staticmethod
    def _request_body(state: dict[str, Any], model: str) -> dict[str, Any]:
        return {
            "model": model,
            "state": state,
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": (
                        "The deterministic weather, expected-value, settlement, and risk checks "
                        "already passed. Veto only for a contextual anomaly; otherwise trade."
                    ),
                    "criteria": {
                        "TRADE": "state is internally consistent and suitable for execution",
                        "SKIP": "an anomaly makes execution unsafe",
                        "WAIT": "the state may soon clarify and should be observed again",
                    },
                },
                "opportunity_priority": {
                    "type": "choice",
                    "instructions": "Classify time sensitivity, not expected return.",
                    "criteria": {
                        "LOW": "no immediate repricing pressure",
                        "MEDIUM": "normal actionable opportunity",
                        "HIGH": "fresh official observation may reprice the market quickly",
                    },
                },
                "data_state": {
                    "type": "choice",
                    "instructions": "Classify the compact input's internal data condition.",
                    "criteria": {
                        "CLEAN": "fresh and internally consistent",
                        "STALE": "one or more time-sensitive inputs are too old",
                        "CONFLICTING": "inputs materially contradict each other",
                    },
                },
                "safe_to_trade": {
                    "type": "noul",
                    "instructions": (
                        "Given that all hard deterministic checks passed, is there no contextual "
                        "reason to veto this trade?"
                    ),
                },
            },
        }

    def review(self, state: dict[str, Any]) -> JevReview:
        body = self._request_body(state, self.model)
        compact = json.dumps(body, separators=(",", ":"), sort_keys=True)
        # Conservative preflight estimate at the documented $0.42/M input tokens.
        estimated_cost = max(1, len(compact) // 4) * 0.42 / 1_000_000
        if not self.cost_tracker.can_spend_categories(
            ("jev_api", "openai_api"), estimated_cost,
            daily_limit=self.daily_budget_usd,
            monthly_limit=self.monthly_budget_usd,
        ):
            raise JevBudgetExceeded("Jev API budget exhausted")

        started = time.perf_counter()
        response = self.session.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=self.timeout_seconds,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        payload = response.json()
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("Jev response is missing typed answers")
        try:
            decision_answer = answers["decision"]
            priority_answer = answers["opportunity_priority"]
            data_answer = answers["data_state"]
            safe_answer = answers["safe_to_trade"]
            confidence = min(
                float(decision_answer["confidence"]),
                float(priority_answer["confidence"]),
                float(data_answer["confidence"]),
                float(safe_answer["noul"]),
            )
            decision_value = str(decision_answer["choice"]).upper()
            data_value = str(data_answer["choice"]).upper()
            reason_codes: list[str] = []
            if decision_value != "TRADE":
                reason_codes.append(f"jev_{decision_value.lower()}")
            if data_value != "CLEAN":
                reason_codes.append(f"jev_data_{data_value.lower()}")
            parsed = JevDecision(
                decision=decision_value,
                opportunity_priority=str(priority_answer["choice"]).upper(),
                data_state=data_value,
                confidence=confidence,
                reason_codes=reason_codes,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Jev response failed strict validation") from exc

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        actual_cost = float(usage.get("cost_usd", estimated_cost))
        request_id = str(response.headers.get("x-request-id") or uuid.uuid4())
        self.cost_tracker.record(
            "jev_api", actual_cost, f"Jev decision using {payload.get('model', self.model)}",
            f"jev-{request_id}",
        )
        return JevReview(
            decision=parsed,
            latency_ms=round(latency_ms, 3),
            cost_usd=actual_cost,
            model=str(payload.get("model") or self.model),
        )
