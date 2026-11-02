import ast
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


_BIAS_SYNONYMS = {
    "bullish": "bullish",
    "bull": "bullish",
    "long": "bullish",
    "up": "bullish",
    "uptrend": "bullish",
    "positive": "bullish",
    "bearish": "bearish",
    "bear": "bearish",
    "short": "bearish",
    "down": "bearish",
    "downtrend": "bearish",
    "negative": "bearish",
    "neutral": "neutral",
    "sideways": "neutral",
    "range": "neutral",
    "ranging": "neutral",
    "flat": "neutral",
    "mixed": "neutral",
    "none": "neutral",
}


class AnalystSubmission(BaseModel):
    """What the analyst model decides; the code adds timeframes and indicator snapshots."""

    higher_timeframe_bias: Literal["bullish", "bearish", "neutral"]
    lower_timeframe_timing: Literal["bullish", "bearish", "neutral"]
    regime: str = "unclear"
    momentum: str = "unclear"
    volatility: str = "unclear"
    key_levels: list[float] = Field(default_factory=list)
    divergences: list[str] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("higher_timeframe_bias", "lower_timeframe_timing", mode="before")
    @classmethod
    def normalize_bias(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return _BIAS_SYNONYMS.get(value.strip().lower(), value)

    @field_validator("regime", "momentum", "volatility", "rationale", mode="before")
    @classmethod
    def text_or_unclear(cls, value: Any) -> Any:
        return "unclear" if value is None else value


class StrategyChoice(BaseModel):
    """The strategy model picks a measured candidate; the code supplies its exact settings."""

    candidate_id: str | None = Field(
        default=None, description="The candidate_id of the chosen eligible_candidates row."
    )
    template_name: str | None = None
    chosen_timeframe: str | None = None
    description: str = Field(
        default="", description="The edge this strategy exploits in the current regime."
    )

    @model_validator(mode="after")
    def names_a_candidate(self) -> "StrategyChoice":
        if not self.candidate_id and not self.template_name:
            raise ValueError("Provide candidate_id, or template_name with chosen_timeframe")
        return self


class MultiTimeframeFindings(BaseModel):
    higher_timeframe_bias: Literal["bullish", "bearish", "neutral"]
    lower_timeframe_timing: Literal["bullish", "bearish", "neutral"]
    higher_timeframe: str
    lower_timeframe: str
    regime: str
    momentum: str
    volatility: str
    key_levels: list[float] = Field(default_factory=list)
    divergences: list[str] = Field(default_factory=list)
    rationale: str = ""
    technical_snapshots: dict[str, dict[str, Any]] = Field(default_factory=dict)


class StrategyDefinition(BaseModel):
    name: str
    description: str
    chosen_timeframe: str
    template_name: Literal[
        "ema_crossover",
        "rsi_mean_reversion",
        "mean_reversion",
        "stat_arb_spread",
        "supertrend_atr",
        "macd_htf_bias",
        "bollinger_squeeze",
        "breakout_confirmed",
        "volatility_fade",
        "carry_funding",
        "regime_gated_trend",
        "htf_trend_ltf_timing",
        "multi_timeframe_hybrid",
    ] = "ema_crossover"
    entry_rule: Literal[
        "ema_crossover",
        "rsi_mean_reversion",
        "mean_reversion",
        "stat_arb_spread",
        "supertrend_atr",
        "macd_htf_bias",
        "bollinger_squeeze",
        "breakout_confirmed",
        "volatility_fade",
        "carry_funding",
        "regime_gated_trend",
        "htf_trend_ltf_timing",
        "multi_timeframe_hybrid",
    ] = "ema_crossover"
    exit_rule: Literal[
        "ema_crossover", "opposite_signal", "fixed_risk_reward", "trailing_atr", "time_stop"
    ] = "opposite_signal"
    fast_period: int = Field(default=12, ge=2)
    slow_period: int = Field(default=26, ge=3)
    stop_loss_pct: float = Field(default=0.02, gt=0, le=0.5)
    take_profit_pct: float | None = Field(default=None, gt=0, le=1)
    position_fraction: float = Field(default=0.1, gt=0, le=1)
    parameters: dict[str, int | float | str] = Field(default_factory=dict)
    time_stop_bars: int | None = Field(default=None, ge=1)
    trailing_atr_multiple: float | None = Field(default=None, gt=0)
    filters: list[Literal["rsi_below_70", "price_above_slow_ema", "volume_positive"]] = Field(
        default_factory=list
    )
    revision_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_periods(self) -> "StrategyDefinition":
        if self.template_name != self.entry_rule:
            raise ValueError(
                "template_name and entry_rule must identify the same supported template"
            )
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        return self


class LiveSignal(BaseModel):
    action: Literal["long", "short", "exit", "hold"]
    reason: str
    strength: float = Field(ge=0, le=1)
    price: float = Field(gt=0)
    stop: float | None = Field(default=None, gt=0)
    strategy_over: bool = False


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
    funding_cost: float = 0.0
    equity_curve: list[dict[str, Any]]
    trades: list[TradeRecord]
    metrics: dict[str, float | int | None]


class Critique(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    issues: list[str] = Field(default_factory=list)
    revision_instructions: str = ""
    rationale: str = ""
    diversity_score: float = Field(default=0.5, ge=0, le=1)
    regime_fit_score: float = Field(default=0.5, ge=0, le=1)
    edge_hypothesis: str = ""

    @field_validator("decision", mode="before")
    @classmethod
    def normalize_decision(cls, value: Any) -> Any:
        # Small models often answer "approve" or "Rejected"; the threshold gate still applies.
        if not isinstance(value, str):
            return value
        word = value.strip().upper()
        if word.startswith("APPROV"):
            return "APPROVE"
        if word.startswith("REJECT"):
            return "REJECT"
        return value

    @field_validator("issues", mode="before")
    @classmethod
    def parse_stringified_issues(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError("issues must be a list or a stringified list") from exc
        if not isinstance(parsed, list):
            raise ValueError("issues must be a list or a stringified list")
        return parsed


class RiskAssessment(BaseModel):
    approved: bool
    side: Literal["long", "short"] = "long"
    position_size: float = Field(ge=0)
    position_notional: float = Field(ge=0)
    risk_amount: float = Field(ge=0)
    max_concurrent_exposure: float = Field(ge=0)
    stop_price: float = Field(gt=0)
    account_constraints: list[str] = Field(default_factory=list)


class ExecutionReport(BaseModel):
    status: Literal[
        "dry_run", "filled", "partially_filled", "rejected", "failed", "hold", "stopped"
    ]
    orders: list[dict[str, Any]] = Field(default_factory=list)
    fills: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    dry_run: bool
    live_signal: LiveSignal | None = None
    execution_result: str | None = None


class OrderSnapshot(BaseModel):
    id: str | None = None
    symbol: str
    type: str | None = None
    side: str | None = None
    status: str
    amount: float | None = None
    price: float | None = None
    average: float | None = None
    filled: float | None = None
    remaining: float | None = None
    cost: float | None = None
    timestamp: int | None = None
    info: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class OrderExecutionResult(BaseModel):
    status: Literal[
        "dry_run", "open", "filled", "partially_filled", "rejected", "failed", "canceled"
    ]
    orders: list[OrderSnapshot] = Field(default_factory=list)
    fills: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    filled: float = Field(default=0, ge=0)
    remaining: float = Field(default=0, ge=0)


class AccountBalance(BaseModel):
    free: dict[str, float | None] = Field(default_factory=dict)
    used: dict[str, float | None] = Field(default_factory=dict)
    total: dict[str, float | None] = Field(default_factory=dict)
    currencies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Bybit's own account payload (account type, equity, per-coin USD value, borrowing).
    info: dict[str, Any] = Field(default_factory=dict)

    def available(self, currency: str) -> float:
        """Free amount of a currency, or 0 when the account holds none."""
        value = self.free.get(currency)
        if value is None:
            value = self.currencies.get(currency, {}).get("free")
        try:
            return max(float(value or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0


def json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    return value
