"""Executable-price expected-value calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.kalshi.fees import trading_fee_usd
from src.kalshi.orderbook import OrderBook, Side


@dataclass(frozen=True, slots=True)
class EdgeEstimate:
    side: Side
    contracts: int
    model_probability: float
    executable_price_cents: float
    market_implied_probability: float
    gross_edge: float
    spread_cents: int | None
    fee_usd: float
    slippage_usd: float
    net_ev_usd: float
    expected_profit_usd: float
    return_on_capital: float
    fill_probability: float
    fillable_contracts: int

    @property
    def positive(self) -> bool:
        return self.net_ev_usd > 0 and self.fillable_contracts >= self.contracts


def calculate_edge(
    *,
    side: Side,
    model_probability: float,
    contracts: int,
    orderbook: OrderBook,
    fill_probability: float = 1.0,
    extra_slippage_cents_per_contract: float = 0.0,
    fee_rate: Decimal = Decimal("0.07"),
) -> EdgeEstimate:
    if not 0 <= model_probability <= 1 or contracts <= 0:
        raise ValueError("invalid probability/contracts")
    if not 0 <= fill_probability <= 1 or extra_slippage_cents_per_contract < 0:
        raise ValueError("invalid fill/slippage input")
    best_ask = orderbook.best_ask(side)
    average_price, fillable = orderbook.executable_buy(side, contracts)
    if best_ask is None or fillable == 0:
        raise ValueError("no executable ask liquidity")
    fee_price = max(1, min(99, round(average_price)))
    fee = float(trading_fee_usd(min(contracts, fillable), fee_price, rate=fee_rate))
    slippage = ((average_price - best_ask + extra_slippage_cents_per_contract)
                * min(contracts, fillable) / 100)
    filled = min(contracts, fillable)
    gross_ev = filled * (model_probability - average_price / 100)
    net_ev = gross_ev - fee - extra_slippage_cents_per_contract * filled / 100
    capital = filled * average_price / 100 + fee
    return EdgeEstimate(
        side=side,
        contracts=contracts,
        model_probability=model_probability,
        executable_price_cents=round(average_price, 4),
        market_implied_probability=round(average_price / 100, 6),
        gross_edge=round(model_probability - average_price / 100, 6),
        spread_cents=orderbook.spread_cents(side),
        fee_usd=round(fee, 2),
        slippage_usd=round(slippage, 4),
        net_ev_usd=round(net_ev, 4),
        expected_profit_usd=round(net_ev * fill_probability, 4),
        return_on_capital=round(net_ev / capital, 6) if capital else 0.0,
        fill_probability=fill_probability,
        fillable_contracts=fillable,
    )

