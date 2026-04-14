"""
APEX Crypto Risk Utilities — shared sizing and exit logic for crypto strategies.
Used by crypto_scalper.py and position_exit.py.
"""


def crypto_compound_bet_usd(
    entry_cents: int,
    buffer_pct: float,
    base_bet_usd: float,
    bet_multiplier: float,
    max_bet_usd: float,
) -> float:
    """
    Size a crypto scalp bet based on signal confidence.
    Larger buffer = more certain = scale up. Entry near 90c = less room = scale down.
    """
    price_room = max(0.0, (95 - entry_cents) / 45.0)
    bet = base_bet_usd * (1.0 + buffer_pct * bet_multiplier) * price_room
    return round(min(max(bet, base_bet_usd * 0.5), max_bet_usd), 2)


def should_exit_crypto(
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
) -> tuple:
    """
    Crypto exit logic. Returns (should_exit: bool, reason: str).
    Exits on: profit target, hard stop, near expiry, or adverse spot + slipped odds.
    """
    if profit_pct >= profit_target:
        return True, f"profit target {profit_pct:.1%}"
    if profit_pct <= stop_loss:
        return True, f"stop-loss {profit_pct:.1%}"
    if hours_left <= near_expiry_hours:
        return True, f"near expiry ({hours_left * 60:.0f} min left)"
    if spot_now > 0 and spot_entry > 0:
        spot_move = (spot_now - spot_entry) / spot_entry
        adverse = (direction == "above" and spot_move < -0.02) or \
                  (direction == "below" and spot_move > 0.02)
        if adverse and current_cents < drop_exit_cents and profit_pct >= break_even_floor:
            return True, (
                f"adverse spot {spot_move:.1%}, odds={current_cents}c < {drop_exit_cents}c"
            )
    return False, ""
