"""Fixed-fractional and calibrated fractional-Kelly sizing."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True, slots=True)
class SizingLimits:
    bankroll_usd: float = 100.0
    max_position_usd: float = 5.0
    max_position_pct: float = 0.05
    max_total_exposure_pct: float = 0.25
    kelly_fraction: float = 0.10
    poor_calibration_multiplier: float = 0.25


def full_kelly_fraction(probability: float, price: float) -> float:
    if not 0 < probability < 1 or not 0 < price < 1:
        return 0.0
    return round(max(0.0, (probability - price) / (1.0 - price)), 12)


def size_contracts(
    *,
    probability: float,
    price_cents: int,
    limits: SizingLimits,
    current_total_exposure_usd: float,
    calibration_quality: float,
    method: str = "fixed_fractional",
) -> int:
    if not 1 <= price_cents <= 99 or limits.bankroll_usd <= 0:
        return 0
    if not 0 <= calibration_quality <= 1:
        return 0
    per_position_cap = min(limits.max_position_usd,
                           limits.bankroll_usd * limits.max_position_pct)
    exposure_room = max(0.0, limits.bankroll_usd * limits.max_total_exposure_pct
                        - current_total_exposure_usd)
    budget = min(per_position_cap, exposure_room)
    if method == "kelly":
        fraction = full_kelly_fraction(probability, price_cents / 100)
        multiplier = (limits.poor_calibration_multiplier
                      + (1 - limits.poor_calibration_multiplier) * calibration_quality)
        budget = min(budget, limits.bankroll_usd * fraction
                     * limits.kelly_fraction * multiplier)
    elif method != "fixed_fractional":
        raise ValueError(f"unknown sizing method: {method}")
    return max(0, floor(budget / (price_cents / 100)))
