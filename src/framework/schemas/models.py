from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OHLCVBar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "OHLCVBar":
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be the greatest OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be the smallest OHLC value")
        return self

    @classmethod
    def from_ccxt(cls, row: list[float | int]) -> "OHLCVBar":
        if len(row) != 6:
            raise ValueError("CCXT OHLCV rows must contain timestamp, OHLC, and volume")
        timestamp = datetime.fromtimestamp(float(row[0]) / 1000, tz=UTC)
        return cls(
            timestamp=timestamp,
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
            volume=row[5],
        )


class MarketDataBundle(BaseModel):
    symbol: str
    timeframes: dict[str, list[OHLCVBar]]
    ticker: dict[str, Any] | None = None
    orderbook: dict[str, Any] | None = None
    recent_trades: list[dict[str, Any]] | None = None


class AnalystFinding(BaseModel):
    timeframe: str
    higher_timeframe_bias: Literal["bullish", "bearish", "neutral"]
    timing_signal: Literal["bullish", "bearish", "neutral"]
    regime: str
    momentum: str
    volatility: str
    key_levels: list[float] = Field(default_factory=list)
    divergences: list[str] = Field(default_factory=list)
    rationale: str
    indicators: dict[str, float | str | None] = Field(default_factory=dict)


class StrategyDefinition(BaseModel):
    name: str
    description: str
    chosen_timeframe: str
    entry_rule: Literal["ema_crossover"]
    exit_rule: Literal["ema_crossover"]
    fast_period: int = Field(default=12, ge=2)
    slow_period: int = Field(default=26, ge=3)
    stop_loss_pct: float = Field(default=0.02, gt=0, le=0.5)
    take_profit_pct: float | None = Field(default=None, gt=0, le=1)
    position_fraction: float = Field(default=0.1, gt=0, le=1)
    filters: list[str] = Field(default_factory=list)
    revision_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_periods(self) -> "StrategyDefinition":
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        return self


class TradeRecord(BaseModel):
    entry_time: datetime
    exit_time: datetime
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    entry_notional: float = Field(ge=0)
    exit_notional: float = Field(ge=0)
    gross_pnl: float
    fees: float = Field(ge=0)
    slippage: float = Field(ge=0)
    net_pnl: float
    exit_reason: str


class BacktestReport(BaseModel):
    initial_capital: float
    final_equity: float
    net_pnl: float
    net_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    total_fees: float
    total_slippage: float
    equity_curve: list[dict[str, Any]]
    trades: list[TradeRecord]
    metrics: dict[str, float | int | None]


class Critique(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    issues: list[str] = Field(default_factory=list)
    revision_instructions: str = ""
    rationale: str


class RiskAssessment(BaseModel):
    approved: bool
    position_size: float = Field(ge=0)
    position_notional: float = Field(ge=0)
    risk_amount: float = Field(ge=0)
    max_concurrent_exposure: float = Field(ge=0)
    stop_price: float = Field(gt=0)
    account_constraints: list[str] = Field(default_factory=list)


class ExecutionReport(BaseModel):
    status: Literal["dry_run", "filled", "partially_filled", "rejected", "failed"]
    orders: list[dict[str, Any]] = Field(default_factory=list)
    fills: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    dry_run: bool


def json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    return value
