"""
Kelly Criterion position sizer for prediction market bets.

kelly_bet(bankroll, our_probability, market_probability, kelly_fraction, max_pct)
  -> dollar amount to bet (0 if no edge)
"""


def kelly_bet(
    bankroll: float,
    our_probability: float,
    market_probability: float,
    kelly_fraction: float = 0.25,
    max_pct: float = 0.05,
) -> float:
    """
    Fractional Kelly position size for a binary prediction market.

    Args:
        bankroll:          Total capital available (USD).
        our_probability:   Our estimated true probability (0–1).
        market_probability: Implied probability from market price (0–1).
        kelly_fraction:    Fraction of full Kelly to use (default 0.25 = quarter-Kelly).
        max_pct:           Hard cap as fraction of bankroll (default 5%).

    Returns:
        Dollar amount to bet. Returns 0.0 if edge is zero or negative.
    """
    edge = our_probability - market_probability
    if edge <= 0:
        return 0.0

    # For a binary market where YES costs p per dollar:
    # Kelly fraction = edge / (1 - market_probability)
    # (simplified: b = 1/market_probability - 1 odds for a YES bet)
    if market_probability >= 1.0:
        return 0.0

    b = (1.0 - market_probability) / market_probability  # net odds for YES bet
    full_kelly_pct = edge / b if b > 0 else 0.0

    fractional_pct = full_kelly_pct * kelly_fraction
    capped_pct = min(fractional_pct, max_pct)

    return round(bankroll * capped_pct, 2)


def implied_probability(price_cents: int) -> float:
    """Convert Kalshi price (0–99 cents) to implied probability."""
    return price_cents / 100.0
