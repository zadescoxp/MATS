from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelRoute(BaseModel):
    provider: str = "baseten"
    model: str = "meta-models/muse-spark-open-1026"
    api_key: SecretStr | None = None
    base_url: str | None = "https://inference.baseten.co/v1"
    supports_tools: bool = True


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
    market_data_testnet: bool = False
    bybit_api_key: SecretStr | None = None
    bybit_api_secret: SecretStr | None = None
    llm_provider: str = "baseten"
    llm_model: str = "meta-models/muse-spark-open-1026"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = "https://inference.baseten.co/v1"
    llm_supports_tools: bool = True
    dry_run: bool = True
    kill_switch: bool = True
    log_level: str = "INFO"
    max_revision_loops: int = Field(default=2, ge=0, le=20)
    min_backtest_trades: int = Field(default=3, ge=1)
    min_strategy_bars: int = Field(default=100, ge=50)
    max_concurrent_symbols: int = Field(default=2, ge=1, le=20)
    max_backtest_drawdown: float = Field(default=0.25, gt=0, le=1)
    min_backtest_net_pnl: float = Field(default=0, ge=0)
    initial_capital: float = Field(default=10_000.0, gt=0)
    fee_rate_taker: float = Field(default=0.001, ge=0, lt=1)
    fee_rate_maker: float = Field(default=0.001, ge=0, lt=1)
    slippage_percent: float = Field(default=0.0005, ge=0, lt=1)
    slippage_fixed: float = Field(default=0.0, ge=0)
    max_risk_per_trade: float = Field(default=0.01, gt=0, le=1)
    max_account_exposure: float = Field(default=0.25, gt=0, le=1)
    edge_fade_lookback: int = Field(default=10, ge=1)
    edge_fade_min_expectancy: float = 0.0
    allow_short: bool = False
    model_routes: dict[str, ModelRoute] = Field(default_factory=dict)
    exchange_rate_limit_ms: int = Field(default=1000, ge=0)
    request_timeout_ms: int = Field(default=10_000, gt=0)
    retry_attempts: int = Field(default=3, ge=1, le=10)
    include_orderbook: bool = False
    include_recent_trades: bool = False
    include_external_context: bool = True
    include_pair_context: bool = True
    execution_timeout_seconds: float = Field(default=10, gt=0)
    execution_poll_interval_seconds: float = Field(default=1, gt=0)

    def model_for(self, role: str) -> ModelRoute:
        route = self.model_routes.get(role) or ModelRoute(
            provider=self.llm_provider,
            model=self.llm_model,
            api_key=self.llm_api_key,
            base_url=self.llm_base_url,
            supports_tools=self.llm_supports_tools,
        )
        if not route.supports_tools:
            raise ValueError(f"Configured model for role {role!r} does not support tool calling")
        return route


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
