"""Strict schema for optional LLM/Jev veto layers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExceptionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: bool
    settlement_interpretation_confidence: float = Field(ge=0.0, le=1.0)
    data_consistent: bool
    anomaly: bool
    reason_codes: list[str] = Field(max_length=20)

    @property
    def veto_codes(self) -> tuple[str, ...]:
        if self.allow and self.data_consistent and not self.anomaly:
            return ()
        return tuple(self.reason_codes or ["exception_reviewer_veto"])


class JevDecision(BaseModel):
    """Bounded rapid decision. It can only return WAIT/SKIP as a veto."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["TRADE", "SKIP", "WAIT"]
    opportunity_priority: Literal["LOW", "MEDIUM", "HIGH"]
    data_state: Literal["CLEAN", "STALE", "CONFLICTING"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list, max_length=10)

    def veto_codes(self, minimum_confidence: float) -> tuple[str, ...]:
        if self.data_state != "CLEAN":
            return (f"jev_data_{self.data_state.lower()}",)
        if self.confidence < minimum_confidence:
            return ("jev_low_confidence",)
        if self.decision != "TRADE":
            return tuple(self.reason_codes or [f"jev_{self.decision.lower()}"])
        return ()

