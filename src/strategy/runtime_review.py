"""Budgeted OpenAI structured-output reviewer for rare ambiguous states."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from src.monitoring.costs import CostTracker
from src.strategy.exception_review import ExceptionReview


class RuntimeReviewBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeReviewResult:
    review: ExceptionReview
    cost_usd: float
    model: str


class RuntimeExceptionReviewer:
    def __init__(
        self,
        *,
        api_key: str,
        cost_tracker: CostTracker,
        daily_budget_usd: float,
        monthly_budget_usd: float,
        model: str = "gpt-6-luna",
        timeout_seconds: float = 5.0,
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when runtime LLM review is enabled")
        self.cost_tracker = cost_tracker
        self.daily_budget_usd = daily_budget_usd
        self.monthly_budget_usd = monthly_budget_usd
        self.model = model
        prices = {
            "gpt-6-luna": (0.10, 0.50),
            "gpt-6-sol": (2.00, 10.00),
        }
        default_input, default_output = prices.get(model, prices["gpt-6-luna"])
        self.input_price_per_million = (
            default_input if input_price_per_million is None else input_price_per_million
        )
        self.output_price_per_million = (
            default_output if output_price_per_million is None else output_price_per_million
        )
        self.client = client or OpenAI(
            api_key=api_key, timeout=timeout_seconds, max_retries=1,
        )

    def review(self, state: dict[str, Any]) -> RuntimeReviewResult:
        compact_state = json.dumps(state, separators=(",", ":"), sort_keys=True)
        # Reserve a conservative mill before the request; actual usage is booked below.
        reserve = max(0.001, len(compact_state) / 4 * self.input_price_per_million / 1_000_000)
        if not self.cost_tracker.can_spend_categories(
            ("jev_api", "openai_api"), reserve,
            daily_limit=self.daily_budget_usd,
            monthly_limit=self.monthly_budget_usd,
        ):
            raise RuntimeReviewBudgetExceeded("runtime OpenAI budget exhausted")
        response = self.client.responses.parse(
            model=self.model,
            reasoning={"effort": "none"},
            input=[
                {
                    "role": "system",
                    "content": (
                        "Review a rare weather-market data ambiguity. Return only the supplied "
                        "structured schema. You may veto but never invent a weather probability, "
                        "approve stale/conflicting data, or override settlement and risk controls."
                    ),
                },
                {"role": "user", "content": compact_state},
            ],
            text_format=ExceptionReview,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("OpenAI exception review returned no structured output")
        usage = response.usage
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost = (
            input_tokens * self.input_price_per_million
            + output_tokens * self.output_price_per_million
        ) / 1_000_000
        self.cost_tracker.record(
            "openai_api", cost, f"Runtime exception review using {self.model}",
            f"openai-{response.id}",
        )
        return RuntimeReviewResult(review=parsed, cost_usd=cost, model=self.model)
