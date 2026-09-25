"""Conservative paper order simulation with partial/resting fills."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from src.kalshi.orderbook import OrderBook, Side


class PaperOrderStatus(StrEnum):
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    RESTING = "resting"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PaperOrderResult:
    status: PaperOrderStatus
    requested_contracts: int
    filled_contracts: int
    average_fill_price_cents: float | None
    resting_contracts: int


class PaperExecutor:
    def __init__(self, resting_fill_probability: float = 0.0) -> None:
        if not 0 <= resting_fill_probability <= 1:
            raise ValueError("resting_fill_probability must be within [0, 1]")
        self.resting_fill_probability = resting_fill_probability

    @staticmethod
    def _deterministic_draw(order_key: str) -> float:
        digest = hashlib.sha256(order_key.encode()).digest()
        return int.from_bytes(digest[:8], "big") / 2**64

    def submit_buy(
        self,
        *,
        order_key: str,
        side: Side,
        limit_price_cents: int,
        contracts: int,
        orderbook: OrderBook,
    ) -> PaperOrderResult:
        if not 1 <= limit_price_cents <= 99 or contracts <= 0:
            raise ValueError("invalid paper order")
        eligible = tuple(level for level in orderbook.asks(side)
                         if level.price_cents <= limit_price_cents)
        remaining = contracts
        filled = 0
        cost_cents = 0
        for level in eligible:
            take = min(remaining, level.quantity)
            filled += take
            cost_cents += take * level.price_cents
            remaining -= take
            if remaining == 0:
                break

        # A non-marketable resting order is not assumed filled. A deterministic
        # draw makes backtests repeatable while preserving missed fills.
        if filled == 0 and self._deterministic_draw(order_key) < self.resting_fill_probability:
            # Cap synthetic fills at 10% to avoid fabricating full liquidity.
            filled = max(1, contracts // 10)
            cost_cents = filled * limit_price_cents
            remaining = contracts - filled

        status = (PaperOrderStatus.FILLED if filled == contracts else
                  PaperOrderStatus.PARTIALLY_FILLED if filled else
                  PaperOrderStatus.RESTING)
        return PaperOrderResult(
            status=status,
            requested_contracts=contracts,
            filled_contracts=filled,
            average_fill_price_cents=(cost_cents / filled if filled else None),
            resting_contracts=remaining,
        )

