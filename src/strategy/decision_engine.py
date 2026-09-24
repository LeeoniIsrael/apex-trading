"""Hard-check decision engine; optional AI layers may only veto its output."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.strategy.edge import EdgeEstimate
from src.strategy.settlement import SettlementSpec


class DecisionAction(StrEnum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    SELL_YES = "SELL_YES"
    SELL_NO = "SELL_NO"
    HOLD = "HOLD"
    SKIP = "SKIP"


@dataclass(frozen=True, slots=True)
class Decision:
    action: DecisionAction
    reason_codes: tuple[str, ...]
    edge: EdgeEstimate | None = None


def decide(
    *,
    settlement: SettlementSpec,
    edge: EdgeEstimate | None,
    data_stale: bool,
    data_conflict: bool,
    model_confidence: float,
    min_model_confidence: float,
    min_net_edge: float,
    max_spread_cents: int,
    min_liquidity_contracts: int,
    risk_reason_codes: tuple[str, ...] = (),
    external_veto_codes: tuple[str, ...] = (),
) -> Decision:
    reasons: list[str] = []
    if not settlement.tradeable:
        reasons.extend(settlement.ambiguity_flags or ("settlement_not_tradeable",))
    if data_stale:
        reasons.append("stale_data")
    if data_conflict:
        reasons.append("conflicting_data")
    if model_confidence < min_model_confidence:
        reasons.append("low_model_confidence")
    reasons.extend(risk_reason_codes)
    reasons.extend(external_veto_codes)
    if edge is None:
        reasons.append("no_executable_price")
    else:
        if edge.net_ev_usd <= 0 or edge.gross_edge < min_net_edge:
            reasons.append("insufficient_net_edge")
        if edge.spread_cents is None or edge.spread_cents > max_spread_cents:
            reasons.append("spread_limit")
        if edge.fillable_contracts < min_liquidity_contracts:
            reasons.append("liquidity_limit")
    if reasons:
        return Decision(DecisionAction.SKIP, tuple(sorted(set(reasons))), edge)
    assert edge is not None
    action = DecisionAction.BUY_YES if edge.side == "yes" else DecisionAction.BUY_NO
    return Decision(action, ("positive_net_ev",), edge)
