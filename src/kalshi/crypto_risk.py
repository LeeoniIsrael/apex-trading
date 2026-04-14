"""Pure risk helpers for crypto scalp entry and exit decisions."""


def crypto_compound_bet_usd(
    entry_cents: int,
    buffer_pct: float,
    base_bet_usd: float,
    bet_multiplier: float,
    max_bet_usd: float,
) -> float:
    """Compute aggressive but capped crypto bet size in USD."""
    target_usd = base_bet_usd * bet_multiplier

    if entry_cents <= 55:
        target_usd *= 1.25

    if buffer_pct >= 0.05:
        target_usd *= 1.25

    target_usd = max(base_bet_usd, target_usd)
    return round(min(target_usd, max_bet_usd), 2)


def should_exit_crypto(
    *,
    direction: str,
    profit_pct: float,
    current_cents: int,
    hours_left: float,
    spot_now: float,
    spot_entry: float,
    profit_target: float,
    stop_loss: float,
    break_even_floor: float,
    drop_exit_cents: int,
    near_expiry_hours: float,
) -> tuple[bool, str]:
    """Decide whether to exit a crypto position under the crypto policy."""
    if profit_pct >= profit_target:
        return True, f"profit target {profit_pct:.1%}"

    if profit_pct <= stop_loss:
        return True, f"crypto stop-loss {profit_pct:.1%}"

    downside_move = False
    if spot_now > 0 and spot_entry > 0:
        if direction == "above":
            downside_move = spot_now < spot_entry
        elif direction == "below":
            downside_move = spot_now > spot_entry

    if downside_move and current_cents < drop_exit_cents:
        if profit_pct >= break_even_floor:
            return True, f"crypto downside + odds<{drop_exit_cents}¢ ({profit_pct:.1%})"

    if hours_left <= near_expiry_hours:
        if profit_pct >= break_even_floor:
            return True, f"near expiry break-even ({hours_left*60:.0f} min left)"
        return True, f"near expiry risk-off ({hours_left*60:.0f} min left)"

    return False, ""
