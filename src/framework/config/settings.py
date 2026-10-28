from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelRoute(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: SecretStr | None = None
    base_url: str | None = None
    supports_tools: bool = True


_DEFAULT_ROUTES = {
    role: ModelRoute() for role in ("analyst", "strategy", "critic", "coder", "dummy")
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MATS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_assignment=True,
    )

    database_path: Path = Path("./data/state.db")
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h", "1d"])
    bybit_testnet: bool = True
    bybit_api_key: SecretStr | None = None
    bybit_api_secret: SecretStr | None = None
    dry_run: bool = True
    kill_switch: bool = True
    log_level: str = "INFO"
    max_revision_loops: int = Field(default=5, ge=0, le=20)
    min_backtest_trades: int = Field(default=3, ge=1)
    max_backtest_drawdown: float = Field(default=0.25, gt=0, le=1)
    min_backtest_net_pnl: float = Field(default=0, ge=0)
    initial_capital: float = Field(default=10_000.0, gt=0)
    fee_rate_taker: float = Field(default=0.001, ge=0, lt=1)
    fee_rate_maker: float = Field(default=0.001, ge=0, lt=1)
    slippage_percent: float = Field(default=0.0005, ge=0, lt=1)
    slippage_fixed: float = Field(default=0.0, ge=0)
    max_risk_per_trade: float = Field(default=0.01, gt=0, le=1)
    max_account_exposure: float = Field(default=0.25, gt=0, le=1)
    model_routes: dict[str, ModelRoute] = Field(default_factory=lambda: _DEFAULT_ROUTES.copy())
    exchange_rate_limit_ms: int = Field(default=1000, ge=0)
    request_timeout_ms: int = Field(default=10_000, gt=0)
    retry_attempts: int = Field(default=3, ge=1, le=10)
    include_orderbook: bool = False
    include_recent_trades: bool = False
    execution_timeout_seconds: float = Field(default=10, gt=0)
    execution_poll_interval_seconds: float = Field(default=1, gt=0)

    def model_for(self, role: str) -> ModelRoute:
        try:
            route = self.model_routes[role]
        except KeyError as exc:
            raise ValueError(f"No model route configured for agent role {role!r}") from exc
        if not route.supports_tools:
            raise ValueError(f"Configured model for role {role!r} does not support tool calling")
        return route


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
