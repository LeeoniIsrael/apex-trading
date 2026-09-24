"""Portfolio-level trade permission checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskState:
    open_markets: int
    total_exposure_usd: float
    correlated_city_exposure_usd: float
    daily_pnl_usd: float
    drawdown_pct: float
    paused: bool = False


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    bankroll_usd: float = 100.0
    max_total_exposure_pct: float = 0.25
    max_correlated_city_exposure_pct: float = 0.10
    max_daily_loss_usd: float = 5.0
    max_drawdown_pct: float = 0.15
    max_open_markets: int = 5


def risk_blocks(state: RiskState, policy: RiskPolicy, new_exposure_usd: float) -> tuple[str, ...]:
    reasons: list[str] = []
    if state.paused:
        reasons.append("trading_paused")
    if state.daily_pnl_usd <= -policy.max_daily_loss_usd:
        reasons.append("daily_loss_limit")
    if state.drawdown_pct >= policy.max_drawdown_pct:
        reasons.append("drawdown_limit")
    if state.open_markets >= policy.max_open_markets:
        reasons.append("open_market_limit")
    if state.total_exposure_usd + new_exposure_usd > policy.bankroll_usd * policy.max_total_exposure_pct:
        reasons.append("total_exposure_limit")
    if state.correlated_city_exposure_usd + new_exposure_usd > policy.bankroll_usd * policy.max_correlated_city_exposure_pct:
        reasons.append("correlated_city_limit")
    return tuple(reasons)

