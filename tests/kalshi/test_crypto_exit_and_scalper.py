from src.kalshi.crypto_risk import crypto_compound_bet_usd, should_exit_crypto


def test_crypto_compound_bet_usd_scales_up_with_buffer_and_cheap_entry() -> None:
    bet = crypto_compound_bet_usd(
        entry_cents=50,
        buffer_pct=0.06,
        base_bet_usd=5.0,
        bet_multiplier=4.0,
        max_bet_usd=30.0,
    )
    assert bet == 30.0


def test_crypto_compound_bet_usd_respects_base_floor() -> None:
    bet = crypto_compound_bet_usd(
        entry_cents=85,
        buffer_pct=0.02,
        base_bet_usd=5.0,
        bet_multiplier=4.0,
        max_bet_usd=30.0,
    )
    assert bet >= 5.0


def test_should_exit_crypto_for_profit_target() -> None:
    should_exit, reason = should_exit_crypto(
        direction="above",
        profit_pct=0.13,
        current_cents=60,
        hours_left=1.0,
        spot_now=69000.0,
        spot_entry=70000.0,
        profit_target=0.12,
        stop_loss=-0.15,
        break_even_floor=0.0,
        drop_exit_cents=50,
        near_expiry_hours=0.33,
    )
    assert should_exit is True
    assert "profit target" in reason


def test_should_exit_crypto_on_downside_and_sub_50_odds_at_breakeven() -> None:
    should_exit, reason = should_exit_crypto(
        direction="above",
        profit_pct=0.00,
        current_cents=49,
        hours_left=1.0,
        spot_now=68000.0,
        spot_entry=70000.0,
        profit_target=0.12,
        stop_loss=-0.15,
        break_even_floor=0.0,
        drop_exit_cents=50,
        near_expiry_hours=0.33,
    )
    assert should_exit is True
    assert "odds<" in reason


def test_should_hold_crypto_if_downside_and_sub_50_but_losing() -> None:
    should_exit, _ = should_exit_crypto(
        direction="above",
        profit_pct=-0.02,
        current_cents=49,
        hours_left=1.0,
        spot_now=68000.0,
        spot_entry=70000.0,
        profit_target=0.12,
        stop_loss=-0.15,
        break_even_floor=0.0,
        drop_exit_cents=50,
        near_expiry_hours=0.33,
    )
    assert should_exit is False


def test_should_exit_crypto_on_stop_loss() -> None:
    should_exit, reason = should_exit_crypto(
        direction="above",
        profit_pct=-0.16,
        current_cents=40,
        hours_left=1.0,
        spot_now=69000.0,
        spot_entry=70000.0,
        profit_target=0.12,
        stop_loss=-0.15,
        break_even_floor=0.0,
        drop_exit_cents=50,
        near_expiry_hours=0.33,
    )
    assert should_exit is True
    assert "stop-loss" in reason


def test_should_exit_crypto_near_expiry_even_if_losing() -> None:
    should_exit, reason = should_exit_crypto(
        direction="above",
        profit_pct=-0.03,
        current_cents=48,
        hours_left=0.2,
        spot_now=0.0,
        spot_entry=70000.0,
        profit_target=0.12,
        stop_loss=-0.15,
        break_even_floor=0.0,
        drop_exit_cents=50,
        near_expiry_hours=0.33,
    )
    assert should_exit is True
    assert "near expiry" in reason


def test_should_exit_crypto_downside_for_below_direction() -> None:
    should_exit, reason = should_exit_crypto(
        direction="below",
        profit_pct=0.01,
        current_cents=49,
        hours_left=1.0,
        spot_now=71000.0,
        spot_entry=70000.0,
        profit_target=0.12,
        stop_loss=-0.15,
        break_even_floor=0.0,
        drop_exit_cents=50,
        near_expiry_hours=0.33,
    )
    assert should_exit is True
    assert "odds<" in reason
