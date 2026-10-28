from pathlib import Path
from typing import Any

from framework.config.settings import Settings
from framework.core.graph import TradingFramework
from framework.persistence.database import Database
from framework.schemas.models import (
    Critique,
    MultiTimeframeFindings,
    OrderExecutionResult,
    OrderSnapshot,
    StrategyDefinition,
)
from framework.tools.market_data import OHLCVResult, TickerSnapshot


class FakeMarketData:
    def __init__(self, candles) -> None:
        self.candles = candles
        self.ohlcv_calls = 0
        self.ticker_calls = 0

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> OHLCVResult:
        self.ohlcv_calls += 1
        return OHLCVResult(symbol=symbol, timeframe=timeframe, candles=self.candles)

    def fetch_realtime_ticker(self, symbol: str) -> TickerSnapshot:
        self.ticker_calls += 1
        return TickerSnapshot(symbol=symbol, last=self.candles[-1].close)

    def fetch_orderbook(self, symbol: str) -> Any:
        raise AssertionError("Order book should be disabled")

    def fetch_recent_trades(self, symbol: str) -> Any:
        raise AssertionError("Recent trades should be disabled")


class FakeLLM:
    def __init__(self, fail_analyst_once: bool = False) -> None:
        self.fail_analyst_once = fail_analyst_once
        self.calls: dict[str, int] = {}

    def invoke_with_tools(self, messages, tools, model_config):
        name = tools[0].name
        self.calls[name] = self.calls.get(name, 0) + 1
        if name == "submit_findings" and self.fail_analyst_once:
            self.fail_analyst_once = False
            raise ConnectionError("temporary model transport issue")
        if name == "submit_findings":
            payload = MultiTimeframeFindings(
                higher_timeframe_bias="bullish",
                lower_timeframe_timing="bullish",
                higher_timeframe="4h",
                lower_timeframe="1h",
                regime="trending",
                momentum="positive",
                volatility="moderate",
                rationale="Higher timeframe and lower timeframe align.",
            ).model_dump(mode="json")
        elif name == "submit_strategy":
            payload = StrategyDefinition(
                name="EMA trend",
                description="Enter on a fast EMA crossover and manage risk with a stop.",
                chosen_timeframe="1h",
                entry_rule="ema_crossover",
                exit_rule="ema_crossover",
                fast_period=2,
                slow_period=5,
                stop_loss_pct=0.2,
                position_fraction=0.1,
            ).model_dump(mode="json")
        elif name == "submit_critique":
            payload = Critique(
                decision="APPROVE",
                rationale="Net-of-cost metrics exceed the configured thresholds.",
            ).model_dump(mode="json")
        else:
            raise AssertionError(f"Unexpected tool {name}")
        return _response(name, payload)


class FakeExecution:
    def __init__(self) -> None:
        self.dry_run_values: list[bool] = []

    def place_order(
        self, symbol, side, amount, order_type, price=None, *, dry_run, idempotency_key
    ):
        self.dry_run_values.append(dry_run)
        status = "dry_run" if dry_run else "filled"
        return OrderExecutionResult(
            status=status,
            orders=[
                OrderSnapshot(
                    symbol=symbol,
                    side=side,
                    amount=amount,
                    status="dry_run" if dry_run else "closed",
                )
            ],
        )

    def place_protective_stop(self, symbol, amount, trigger_price, *, dry_run, idempotency_key):
        return OrderExecutionResult(
            status="dry_run",
            orders=[
                OrderSnapshot(
                    symbol=symbol,
                    side="sell",
                    amount=amount,
                    price=trigger_price,
                    status="dry_run",
                )
            ],
        )


def _response(name: str, payload: dict[str, Any]):
    from framework.llm.client import LLMResponse, ToolCall

    return LLMResponse(
        tool_calls=[ToolCall(id="call-1", name=name, arguments=payload)],
        prompt_tokens=100,
        completion_tokens=25,
        cost=0.001,
    )


def _framework(candles, *, fail_analyst_once: bool = False):
    settings = Settings(
        database_path=Path(":memory:"),
        timeframes=["1h", "4h"],
        min_backtest_trades=1,
        max_backtest_drawdown=1,
        retry_attempts=1,
        dry_run=False,
        kill_switch=True,
    )
    database = Database(":memory:")
    market = FakeMarketData(candles)
    llm = FakeLLM(fail_analyst_once)
    execution = FakeExecution()
    framework = TradingFramework(
        settings,
        database=database,
        market_data=market,
        llm=llm,
        execution=execution,
    )
    return framework, database, market, llm, execution


def test_full_graph_uses_one_analyst_and_dry_run(candles) -> None:
    framework, database, market, llm, execution = _framework(candles)
    try:
        result = framework.run("BTC/USDT")
        assert result.status == "completed", result.errors
        assert result.critique and result.critique.decision == "APPROVE"
        assert result.risk_assessment and result.risk_assessment.approved
        assert result.execution_result and result.execution_result.dry_run
        assert len(result.execution_result.orders) == 2
        assert execution.dry_run_values == [True]
        assert market.ohlcv_calls == 2
        assert market.ticker_calls == 1
        assert llm.calls["submit_findings"] == 1
        assert result.backtest_report and result.backtest_report.total_trades >= 1
        assert result.backtest_report.net_pnl > 0
        analyst_step = database.connection.execute(
            "SELECT tokens_used, cost FROM agent_steps WHERE agent_name='multi_timeframe_analyst'"
        ).fetchone()
        assert analyst_step["tokens_used"] == 125
        assert analyst_step["cost"] == 0.001
    finally:
        framework.close()
        database.close()


def test_resume_does_not_refetch_completed_data_or_repeat_finished_agents(candles) -> None:
    framework, database, market, llm, execution = _framework(candles, fail_analyst_once=True)
    try:
        first = framework.run("BTC/USDT")
        assert first.status == "failed"
        assert market.ohlcv_calls == 2
        llm.fail_analyst_once = False

        resumed = framework.resume(first.run_id)
        assert resumed.status == "completed", resumed.errors
        assert market.ohlcv_calls == 2
        assert market.ticker_calls == 1
        assert llm.calls["submit_findings"] == 2
        assert llm.calls["submit_strategy"] == 1
        assert execution.dry_run_values == [True]
        data_steps = database.connection.execute(
            "SELECT status FROM agent_steps WHERE agent_name='data_worker'"
        ).fetchall()
        assert len(data_steps) == 1
        assert data_steps[0]["status"] == "completed"
    finally:
        framework.close()
        database.close()
