from src.kalshi.kelly import kelly_bet
import pytest


def test_kelly_bet_returns_zero_without_positive_edge() -> None:
    assert kelly_bet(150.0, our_probability=0.50, market_probability=0.50) == 0.0


def test_kelly_bet_respects_protected_bankroll() -> None:
    bet = kelly_bet(
        bankroll=150.0,
        our_probability=0.65,
        market_probability=0.50,
        kelly_fraction=0.60,
        max_pct=0.20,
        protected_bankroll_pct=0.35,
    )
    # Full Kelly at these odds is 15%; fractional is 9%; bet on deployable $97.50.
    assert bet == 8.78


def test_kelly_bet_caps_by_max_pct_on_deployable_bankroll() -> None:
    bet = kelly_bet(
        bankroll=150.0,
        our_probability=0.90,
        market_probability=0.50,
        kelly_fraction=1.00,
        max_pct=0.20,
        protected_bankroll_pct=0.35,
    )
    # Cap should be 20% of deployable bankroll = 0.2 * 97.5 = 19.5
    assert bet == 19.5


def test_kelly_bet_rejects_invalid_inputs() -> None:
    assert kelly_bet(-1.0, 0.60, 0.50) == 0.0
    assert kelly_bet(150.0, 0.0, 0.50) == 0.0
    assert kelly_bet(150.0, 0.60, 1.0) == 0.0
    assert kelly_bet(150.0, 0.60, 0.50, kelly_fraction=0.0) == 0.0
    assert kelly_bet(150.0, 0.60, 0.50, max_pct=0.0) == 0.0


@pytest.mark.parametrize(
    "protected_pct,expected",
    [
        (0.0, 13.5),
        (1.0, 0.0),
        (1.2, 0.0),
        (-0.2, 13.5),
    ],
)
def test_kelly_bet_protected_bankroll_boundaries(
    protected_pct: float,
    expected: float,
) -> None:
    bet = kelly_bet(
        bankroll=150.0,
        our_probability=0.65,
        market_probability=0.50,
        kelly_fraction=0.60,
        max_pct=0.20,
        protected_bankroll_pct=protected_pct,
    )
    assert bet == expected
