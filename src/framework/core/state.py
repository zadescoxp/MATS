from typing import Any, Literal

from pydantic import BaseModel, Field

from framework.schemas.models import (
    BacktestReport,
    Critique,
    ExecutionReport,
    LiveSignal,
    MultiTimeframeFindings,
    OHLCVBar,
    RiskAssessment,
    StrategyDefinition,
)


class GraphState(BaseModel):
    run_id: str
    symbol: str
    timeframes_to_consider: list[str]
    raw_data: dict[str, list[OHLCVBar]] = Field(default_factory=dict)
    market_context: dict[str, Any] = Field(default_factory=dict)
    analyst_findings: list[MultiTimeframeFindings] = Field(default_factory=list)
    strategy_diagnostics: dict[str, Any] = Field(default_factory=dict)
    strategy: StrategyDefinition | None = None
    backtest_report: BacktestReport | None = None
    critique: Critique | None = None
    revision_count: int = Field(default=0, ge=0)
    risk_assessment: RiskAssessment | None = None
    live_signal: LiveSignal | None = None
    generated_code: str | None = None
    execution_result: ExecutionReport | None = None
    errors: list[str] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["running", "completed", "failed", "stopped"] = "running"
    account_equity: float | None = Field(default=None, gt=0)
