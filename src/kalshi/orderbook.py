"""Correct bid-only Kalshi orderbook interpretation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

Side = Literal["yes", "no"]


@dataclass(frozen=True, slots=True)
class PriceLevel:
    price_cents: int
    quantity: int


@dataclass(frozen=True, slots=True)
class OrderBook:
    yes_bids: tuple[PriceLevel, ...]
    no_bids: tuple[PriceLevel, ...]

    def bids(self, side: Side) -> tuple[PriceLevel, ...]:
        return self.yes_bids if side == "yes" else self.no_bids

    def asks(self, side: Side) -> tuple[PriceLevel, ...]:
        opposing = self.no_bids if side == "yes" else self.yes_bids
        return tuple(sorted(
            (PriceLevel(100 - level.price_cents, level.quantity) for level in opposing),
            key=lambda level: level.price_cents,
        ))

    def best_bid(self, side: Side) -> int | None:
        levels = self.bids(side)
        return max((level.price_cents for level in levels), default=None)

    def best_ask(self, side: Side) -> int | None:
        levels = self.asks(side)
        return levels[0].price_cents if levels else None

    def executable_buy(self, side: Side, contracts: int) -> tuple[float, int]:
        """Return volume-weighted ask cents and fillable quantity."""
        remaining = max(0, contracts)
        notional = 0
        filled = 0
        for level in self.asks(side):
            take = min(remaining, level.quantity)
            notional += take * level.price_cents
            filled += take
            remaining -= take
            if remaining == 0:
                break
        return ((notional / filled) if filled else 0.0, filled)

    def spread_cents(self, side: Side) -> int | None:
        bid, ask = self.best_bid(side), self.best_ask(side)
        return None if bid is None or ask is None else ask - bid


def _cents(value: Any, *, dollars: bool) -> int:
    number = Decimal(str(value))
    result = int((number * 100).to_integral_value()) if dollars else int(number)
    if not 1 <= result <= 99:
        raise ValueError(f"orderbook price out of range: {value}")
    return result


def parse_orderbook(payload: dict[str, Any]) -> OrderBook:
    fixed = payload.get("orderbook_fp") or {}
    legacy = payload.get("orderbook") or payload
    if fixed:
        yes_raw, no_raw, dollars = fixed.get("yes_dollars", []), fixed.get("no_dollars", []), True
    else:
        yes_raw, no_raw, dollars = legacy.get("yes", []), legacy.get("no", []), False

    def levels(rows: list[Any]) -> tuple[PriceLevel, ...]:
        parsed: list[PriceLevel] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            try:
                price = _cents(row[0], dollars=dollars)
                quantity = int(Decimal(str(row[1])))
            except (ValueError, TypeError):
                continue
            if quantity > 0:
                parsed.append(PriceLevel(price, quantity))
        return tuple(sorted(parsed, key=lambda item: item.price_cents, reverse=True))

    return OrderBook(yes_bids=levels(yes_raw), no_bids=levels(no_raw))

