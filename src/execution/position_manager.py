"""Fundamental expected-value exits for binary weather positions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.kalshi.fees import trading_fee_usd


@dataclass(frozen=True, slots=True)
class ExitEvaluation:
    should_exit: bool
    expected_hold_value_usd: float
    immediate_sale_value_usd: float
    exit_fee_usd: float
    advantage_usd: float


def evaluate_exit(
    *,
    contracts: int,
    model_probability: float,
    executable_bid_cents: int | None,
    minimum_advantage_usd: float = 0.05,
    fee_rate: Decimal = Decimal("0.07"),
) -> ExitEvaluation:
    if contracts <= 0 or not 0 <= model_probability <= 1 or executable_bid_cents is None:
        return ExitEvaluation(False, 0.0, 0.0, 0.0, 0.0)
    fee = float(trading_fee_usd(contracts, executable_bid_cents, rate=fee_rate))
    hold = contracts * model_probability
    sale = contracts * executable_bid_cents / 100 - fee
    advantage = sale - hold
    return ExitEvaluation(
        should_exit=advantage >= minimum_advantage_usd,
        expected_hold_value_usd=round(hold, 4),
        immediate_sale_value_usd=round(sale, 4),
        exit_fee_usd=round(fee, 2),
        advantage_usd=round(advantage, 4),
    )
