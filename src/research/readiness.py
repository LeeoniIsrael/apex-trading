"""Evidence-based paper-to-live promotion gate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    market_snapshots: int
    resolved_markets: int
    brier_score: float | None
    net_ev_usd: float
    realized_paper_pnl_usd: float
    max_drawdown_pct: float
    unresolved_parser_failures: int
    major_data_incidents: int


@dataclass(frozen=True, slots=True)
class ReadinessPolicy:
    min_market_snapshots: int = 5_000
    min_resolved_markets: int = 200
    max_brier_score: float = 0.20
    max_drawdown_pct: float = 0.15


def evaluate_readiness(evidence: ReadinessEvidence, policy: ReadinessPolicy) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    if evidence.market_snapshots < policy.min_market_snapshots:
        failures.append("insufficient_market_snapshots")
    if evidence.resolved_markets < policy.min_resolved_markets:
        failures.append("insufficient_resolved_markets")
    if evidence.brier_score is None or evidence.brier_score > policy.max_brier_score:
        failures.append("calibration_gate")
    if evidence.net_ev_usd <= 0:
        failures.append("nonpositive_net_ev")
    if evidence.realized_paper_pnl_usd <= 0:
        failures.append("nonpositive_paper_pnl")
    if evidence.max_drawdown_pct > policy.max_drawdown_pct:
        failures.append("drawdown_gate")
    if evidence.unresolved_parser_failures:
        failures.append("settlement_parser_failures")
    if evidence.major_data_incidents:
        failures.append("data_quality_incidents")
    return not failures, tuple(failures)

