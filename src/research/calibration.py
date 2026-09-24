"""Probability calibration metrics without optimistic small-sample claims."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CalibrationMetrics:
    samples: int
    brier_score: float | None
    log_loss: float | None


def calibration_metrics(predictions: list[tuple[float, int]]) -> CalibrationMetrics:
    if not predictions:
        return CalibrationMetrics(0, None, None)
    for probability, outcome in predictions:
        if not 0 <= probability <= 1 or outcome not in {0, 1}:
            raise ValueError("invalid calibration sample")
    brier = sum((p - y) ** 2 for p, y in predictions) / len(predictions)
    eps = 1e-12
    loss = -sum(y * math.log(max(eps, p)) + (1 - y) * math.log(max(eps, 1 - p))
                for p, y in predictions) / len(predictions)
    return CalibrationMetrics(len(predictions), round(brier, 8), round(loss, 8))

