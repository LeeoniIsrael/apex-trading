"""Configurable Kalshi event-contract fee calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING


def trading_fee_usd(
    contracts: int,
    price_cents: int,
    *,
    rate: Decimal = Decimal("0.07"),
) -> Decimal:
    """General taker fee: ceil-to-cent(rate * C * P * (1-P)).

    Market-specific schedules must provide their own rate; the default is not
    assumed authoritative for special-fee markets.
    """
    if contracts < 0 or not 1 <= price_cents <= 99 or rate < 0:
        raise ValueError("invalid fee inputs")
    price = Decimal(price_cents) / Decimal(100)
    raw = rate * Decimal(contracts) * price * (Decimal(1) - price)
    return raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def maker_fee_usd(
    contracts: int,
    price_cents: int,
    *,
    maker_rate: Decimal = Decimal("0"),
) -> Decimal:
    return trading_fee_usd(contracts, price_cents, rate=maker_rate)

