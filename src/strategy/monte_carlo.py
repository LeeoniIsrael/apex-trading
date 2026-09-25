"""Deterministic Monte Carlo for the unobserved remainder of a weather day."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, log2, sqrt

import numpy as np

from src.strategy.settlement import SettlementSpec, value_in_contract


@dataclass(frozen=True, slots=True)
class ProbabilityEstimate:
    probability: float
    confidence_low: float
    confidence_high: float
    entropy_bits: float
    simulations: int
    model_uncertainty_f: float
    impossible: bool = False


def _wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def simulate_contract_probability(
    *,
    spec: SettlementSpec,
    high_so_far_f: float | None,
    ensemble_remaining_highs_f: list[float],
    simulations: int = 10_000,
    forecast_error_std_f: float = 2.0,
    station_bias_f: float = 0.0,
    calibration_shrink: float = 0.10,
    seed: int = 0,
    observation_error_std_f: float = 1.0,
) -> ProbabilityEstimate:
    if not spec.tradeable:
        raise ValueError(f"untradeable settlement spec: {spec.ambiguity_flags}")
    if simulations < 100:
        raise ValueError("simulations must be at least 100")
    if not ensemble_remaining_highs_f and high_so_far_f is None:
        raise ValueError("at least one observed or forecast temperature is required")

    is_low = bool(spec.measurement and spec.measurement.value == "low")
    values = [forecast_error_std_f, station_bias_f, calibration_shrink,
              observation_error_std_f, *ensemble_remaining_highs_f]
    if high_so_far_f is not None:
        values.append(high_so_far_f)
    if (not all(isfinite(v) for v in values) or forecast_error_std_f < 0
        or observation_error_std_f < 0 or not 0.01 <= calibration_shrink <= 1):
        raise ValueError("invalid model inputs or uncertainty")
    # Preliminary observations are not final settlement truth. Crossing a
    # threshold cannot bypass shrinkage or imply a risk-free position.
    rng = np.random.default_rng(seed)
    if ensemble_remaining_highs_f:
        base = rng.choice(np.asarray(ensemble_remaining_highs_f, dtype=float), simulations)
        sampled = base + station_bias_f + rng.normal(0.0, forecast_error_std_f, simulations)
    else:
        sampled = np.full(simulations, float(high_so_far_f))
    if high_so_far_f is not None:
        observed = high_so_far_f + rng.normal(0.0, observation_error_std_f, simulations)
        sampled = (np.minimum(sampled, observed) if is_low else np.maximum(sampled, observed))

    # Model the published integer Fahrenheit value, not a continuous temperature.
    # Half-away-from-zero matches ordinary published integer rounding. Source
    # revision uncertainty remains in observed noise and probability shrinkage.
    sampled = np.sign(sampled) * np.floor(np.abs(sampled) + 0.5)

    successes = int(sum(value_in_contract(float(value), spec) for value in sampled))
    raw = successes / simulations
    # Small shrinkage prevents raw ensemble/Monte Carlo fractions from being
    # presented as perfectly calibrated certainty before empirical calibration.
    probability = raw * (1 - calibration_shrink) + 0.5 * calibration_shrink
    low, high = _wilson(successes, simulations)
    # These bounds describe simulation sampling error, not forecast accuracy.
    low = low * (1 - calibration_shrink) + 0.5 * calibration_shrink
    high = high * (1 - calibration_shrink) + 0.5 * calibration_shrink
    entropy = 0.0 if probability in (0.0, 1.0) else (
        -probability * log2(probability) - (1 - probability) * log2(1 - probability)
    )
    return ProbabilityEstimate(
        probability=round(probability, 6),
        confidence_low=round(low, 6),
        confidence_high=round(high, 6),
        entropy_bits=round(entropy, 6),
        simulations=simulations,
        model_uncertainty_f=forecast_error_std_f,
    )
