from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Alpaca
    alpaca_api_key: str = Field(..., description="Alpaca API key")
    alpaca_secret_key: str = Field(..., description="Alpaca secret key")
    alpaca_base_url: str = Field(
        default="https://paper-api.alpaca.markets",
        description="Alpaca base URL — paper until April 15",
    )

    # Anthropic
    anthropic_api_key: str = Field(..., description="Anthropic API key")

    # APEX limits
    apex_live_cap_usd: float = Field(
        default=150.0,
        description="Hard dollar cap for live trading phase (April 15–30)",
    )
    apex_max_positions: int = Field(default=10, description="Max concurrent positions")
    apex_max_position_pct: float = Field(
        default=0.05, description="Max position size as fraction of portfolio"
    )
    apex_trailing_stop_pct: float = Field(
        default=0.02, description="Trailing stop-loss percentage"
    )


settings = Settings()


def get_settings() -> Settings:
    return settings
