"""Environment configuration for the weather service."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WeatherSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    trading_mode: Literal["paper", "live"] = "paper"
    bankroll: float = Field(default=100.0, gt=0)
    database_path: Path = Path("data/apex_weather.sqlite3")
    kalshi_base_url: str = "https://external-api.kalshi.com/trade-api/v2"
    kalshi_api_key_id: str = ""
    kalshi_private_key_path: Path | None = None
    live_enablement_path: Path = Path("data/LIVE_ENABLED")
    live_database_path: Path = Path("data/apex_live.sqlite3")
    live_capital_limit_usd: float = Field(default=100.0, gt=0, le=100)
    live_max_order_usd: float = Field(default=2.0, gt=0)
    live_max_exposure_usd: float = Field(default=10.0, gt=0)
    live_max_city_exposure_usd: float = Field(default=5.0, gt=0)
    live_max_daily_loss_usd: float = Field(default=2.0, gt=0)
    live_max_drawdown: float = Field(default=.10, gt=0, lt=1)
    live_max_open_positions: int = Field(default=3, gt=0)
    live_max_daily_orders: int = Field(default=10, gt=0)
    live_balance_floor_usd: float = Field(default=20, gt=0)
    min_net_edge: float = Field(default=0.05, ge=0, le=1)
    min_model_confidence: float = Field(default=0.70, ge=0, le=1)
    max_spread_cents: int = Field(default=8, ge=0, le=99)
    min_liquidity: int = Field(default=10, ge=1)
    max_forecast_disagreement_f: float = Field(default=10.0, ge=0)
    monte_carlo_simulations: int = Field(default=10_000, ge=100)
    max_ai_daily_usd: float = Field(default=0.10, ge=0)
    max_ai_monthly_usd: float = Field(default=1.00, ge=0)
    jev_enabled: bool = False
    jev_api_key: str = ""
    jev_endpoint: str = "https://jevtypesafeai.com/api/v1/decide"
    jev_model: str = "jev-latest"
    jev_min_confidence: float = Field(default=0.75, ge=0, le=1)
    jev_obvious_edge: float = Field(default=0.15, ge=0, le=1)
    runtime_llm_enabled: bool = False
    openai_api_key: str = ""
    runtime_llm_model: Literal["gpt-6-luna", "gpt-6-sol"] = "gpt-6-luna"
    llm_review_disagreement_f: float = Field(default=7.0, ge=0)
    nws_user_agent: str = "APEX-Weather/2.0 contact=operator"
    monthly_vps_cost_usd: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def enforce_live_gate(self) -> "WeatherSettings":
        if self.jev_enabled and not self.jev_api_key:
            raise ValueError("JEV_API_KEY is required when JEV_ENABLED=true")
        if self.runtime_llm_enabled and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when RUNTIME_LLM_ENABLED=true")
        if self.trading_mode == "live" and not self.live_enablement_path.is_file():
            raise ValueError(
                "live mode requires an explicit persisted enablement record; "
                "run the documented enable-live command after validation"
            )
        return self
