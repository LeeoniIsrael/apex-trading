"""Budgeted OpenAI structured-output reviewer for rare ambiguous states."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
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
            api_key=api_key, timeout=timeout_seconds, max_retries=0,
        )

    def review(self, state: dict[str, Any]) -> RuntimeReviewResult:
        compact_state = json.dumps(state, separators=(",", ":"), sort_keys=True)
        # Reserve schema/prompt overhead and the maximum output before submission.
        reserve = ((len(compact_state.encode('utf-8')) + 4096) * self.input_price_per_million
                   + 512 * self.output_price_per_million) / 1_000_000
        reservation='openai-reservation-'+str(uuid.uuid4())
        with self.cost_tracker.database.transaction() as c:
            c.execute('BEGIN IMMEDIATE')
            if not self.cost_tracker.can_spend_categories(
                ('jev_api','openai_api'), reserve,
                daily_limit=self.daily_budget_usd, monthly_limit=self.monthly_budget_usd):
                raise RuntimeReviewBudgetExceeded('runtime OpenAI budget exhausted')
            c.execute('INSERT INTO costs(incurred_at,category,amount_usd,description,external_id) VALUES(?,?,?,?,?)',
                      (datetime.now(timezone.utc).isoformat(),'openai_api',reserve,'Runtime review budget reservation',reservation))
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
            max_output_tokens=512,
            store=False,
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
        if usage is None:
            raise ValueError('OpenAI review usage is unknown; budget remains reserved')
        with self.cost_tracker.database.transaction() as c:
            c.execute('UPDATE costs SET amount_usd=?,description=? WHERE external_id=?',
                      (cost,'Runtime review measured usage',reservation))
        return RuntimeReviewResult(review=parsed, cost_usd=cost, model=self.model)
