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
    # Closed candles are cached here so runs only download candles they do not have yet.
    candle_cache_enabled: bool = True
    candle_cache_path: Path = Path("./data/candles.db")
    candle_refresh_bars: int = Field(default=2, ge=1, le=50)
    # Research history per timeframe: at least this many days, never fewer than 500 candles,
    # capped so short timeframes stay fast to backtest.
    min_history_days: float = Field(default=180, ge=0, le=3650)
    max_history_bars: int = Field(default=10_000, ge=500, le=20_000)
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
    # Ollama defaults to a ~4k-token window, which truncates agent prompts and tool schemas.
    ollama_num_ctx: int = Field(default=16_384, ge=2_048, le=131_072)
    ollama_max_output_tokens: int = Field(default=2_048, ge=256, le=32_768)
    dry_run: bool = True
    kill_switch: bool = True
    log_level: str = "INFO"
    max_revision_loops: int = Field(default=2, ge=0, le=20)
    min_backtest_trades: int = Field(default=3, ge=1)
    min_strategy_bars: int = Field(default=100, ge=50)
    max_concurrent_symbols: int = Field(default=2, ge=1, le=20)
    max_backtest_drawdown: float = Field(default=0.25, gt=0, le=1)
    min_backtest_net_pnl: float = Field(default=0, ge=0)
    # Walk-forward: candidates are chosen on the first part of the history and must also
    # be profitable on the unseen remainder before the critique can approve them.
    walk_forward_split: float = Field(default=0.7, ge=0.5, le=0.9)
    require_out_of_sample_edge: bool = True
    # Gross profit must be at least this multiple of the fees and slippage paid.
    min_cost_edge_ratio: float = Field(default=2.0, ge=0)
    # When every measured check passes, the model's critique is advisory and the strategy
    # goes on to the forward test. True lets the model's REJECT veto it anyway.
    critic_can_veto: bool = False
    # Processes that measure strategy candidates in parallel; 1 measures in-process.
    backtest_workers: int = Field(default=4, ge=1, le=32)
    # Closed paper trades on candles after approval before an algorithm may place live
    # orders; 0 trades live as soon as it is approved.
    forward_test_trades: int = Field(default=3, ge=0, le=50)
    initial_capital: float = Field(default=10_000.0, gt=0)
    fee_rate_taker: float = Field(default=0.001, ge=0, lt=1)
    fee_rate_maker: float = Field(default=0.001, ge=0, lt=1)
    slippage_percent: float = Field(default=0.0005, ge=0, lt=1)
    slippage_fixed: float = Field(default=0.0, ge=0)
    # Bybit linear perpetual fees (non-VIP); spot uses fee_rate_taker / fee_rate_maker.
    perpetual_fee_rate_taker: float = Field(default=0.00055, ge=0, lt=1)
    perpetual_fee_rate_maker: float = Field(default=0.0002, ge=0, lt=1)
    max_risk_per_trade: float = Field(default=0.01, gt=0, le=1)
    max_account_exposure: float = Field(default=0.25, gt=0, le=1)
    edge_fade_lookback: int = Field(default=10, ge=1)
    edge_fade_min_expectancy: float = 0.0
    edge_fade_min_trades: int = Field(default=3, ge=1)
    # Perpetual strategies may open shorts. Spot never shorts: it only sells what it holds.
    allow_short: bool = True
    # Spot margin: 1 trades only the account's own money. Above 1, long entries may borrow
    # the quote currency up to this multiple of the free balance (Bybit Unified accounts).
    # On perpetuals the same multiple caps position size against the free margin; the
    # contract leverage set on Bybit must be at least this high.
    spot_leverage: float = Field(default=1.0, ge=1.0, le=3.0)
    model_routes: dict[str, ModelRoute] = Field(default_factory=dict)
    # None keeps ccxt's per-exchange throttle (20ms for Bybit, scaled by endpoint cost).
    # ccxt multiplies this by each endpoint's cost, so 1000 meant ~5s per Bybit request.
    exchange_rate_limit_ms: int | None = Field(default=None, ge=0)
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
