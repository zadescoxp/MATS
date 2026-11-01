import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from framework.agents.common import parse_tool_response
from framework.config.settings import Settings
from framework.core.graph import TradingFramework
from framework.llm.client import LLMResponse, ToolCall
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


def test_analyst_tool_response_accepts_missing_optional_rationale() -> None:
    response = LLMResponse(
        tool_calls=[
            ToolCall(
                id="findings",
                name="submit_findings",
                arguments={
                    "higher_timeframe_bias": "bearish",
                    "lower_timeframe_timing": "neutral",
                    "higher_timeframe": "4h",
                    "lower_timeframe": "1h",
                    "regime": "trending",
                    "momentum": "negative",
                    "volatility": "high",
                },
            )
        ]
    )

    finding = parse_tool_response(response, "submit_findings", MultiTimeframeFindings)

    assert finding.higher_timeframe_bias == "bearish"
    assert finding.volatility == "high"
    assert finding.rationale == ""


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
            request = json.loads(messages[-1]["content"])
            candidate = next(
                item for item in request["eligible_candidates"] if item["timeframe"] == "1h"
            )
            payload = StrategyDefinition(
                name="EMA trend",
                description="Enter on a fast EMA crossover and manage risk with a stop.",
                chosen_timeframe=candidate["timeframe"],
                template_name=candidate["template_name"],
                entry_rule=candidate["template_name"],
                exit_rule=candidate["exit_rule"],
                fast_period=candidate["fast_period"],
                slow_period=candidate["slow_period"],
                parameters=candidate["parameters"],
                time_stop_bars=candidate["time_stop_bars"],
                trailing_atr_multiple=candidate["trailing_atr_multiple"],
                filters=candidate["filters"],
                stop_loss_pct=candidate["stop_loss_pct"],
                take_profit_pct=candidate["take_profit_pct"],
                position_fraction=candidate["position_fraction"],
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

    def place_protective_stop(
        self, symbol, amount, trigger_price, *, dry_run, idempotency_key, side="sell"
    ):
        return OrderExecutionResult(
            status="dry_run",
            orders=[
                OrderSnapshot(
                    symbol=symbol,
                    side=side,
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


def _framework(
    candles,
    *,
    fail_analyst_once: bool = False,
    max_revision_loops: int = 2,
    min_backtest_net_pnl: float = 0,
    stop_event: threading.Event | None = None,
):
    settings = Settings(
        database_path=Path(":memory:"),
        timeframes=["1h", "4h"],
        min_backtest_trades=1,
        max_backtest_drawdown=1,
        max_revision_loops=max_revision_loops,
        min_strategy_bars=100,
        min_backtest_net_pnl=min_backtest_net_pnl,
        retry_attempts=1,
        dry_run=False,
        kill_switch=True,
        include_external_context=False,
        include_pair_context=False,
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
        stop_event=stop_event,
    )
    return framework, database, market, llm, execution


def test_full_graph_uses_one_analyst_and_dry_run(candles) -> None:
    framework, database, market, llm, execution = _framework(candles)
    try:
        result = framework.run("BTC/USDT", {"dashboard_batch_id": "batch-1"})
        assert result.status == "completed", result.errors
        saved_config = database.connection.execute(
            "SELECT config_snapshot FROM runs WHERE run_id = ?", (result.run_id,)
        ).fetchone()
        assert json.loads(saved_config["config_snapshot"])["dashboard_batch_id"] == "batch-1"
        assert result.critique and result.critique.decision == "APPROVE"
        assert result.risk_assessment and not result.risk_assessment.approved
        assert result.live_signal and result.live_signal.action == "hold"
        assert result.execution_result and result.execution_result.status == "hold"
        assert result.execution_result.orders == []
        assert execution.dry_run_values == []
        assert market.ohlcv_calls == 4
        assert market.ticker_calls == 1
        assert llm.calls["submit_findings"] == 1
        assert result.backtest_report and result.backtest_report.total_trades >= 1
        assert result.backtest_report.net_pnl > 0
        analyst_step = database.connection.execute(
            """SELECT tokens_used, cost, tool_calls_json FROM agent_steps
               WHERE agent_name='multi_timeframe_analyst'"""
        ).fetchone()
        assert analyst_step["tokens_used"] == 125
        assert analyst_step["cost"] == 0.001
        tool_calls = json.loads(analyst_step["tool_calls_json"])
        assert tool_calls[0]["name"] == "submit_findings"
        assert tool_calls[0]["arguments"]["higher_timeframe_bias"] == "bullish"
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
        assert market.ohlcv_calls == 4
        assert market.ticker_calls == 1
        assert llm.calls["submit_findings"] == 2
        assert llm.calls["submit_strategy"] == 1
        assert execution.dry_run_values == []
        data_steps = database.connection.execute(
            "SELECT status FROM agent_steps WHERE agent_name='data_worker'"
        ).fetchall()
        assert len(data_steps) == 1
        assert data_steps[0]["status"] == "completed"
    finally:
        framework.close()
        database.close()


def test_checkpoints_reference_candles_and_resume_restores_them_exactly(candles) -> None:
    framework, database, _market, llm, _execution = _framework(candles, fail_analyst_once=True)
    try:
        first = framework.run("BTC/USDT")
        assert first.status == "failed"
        llm.fail_analyst_once = False

        resumed = framework.resume(first.run_id)

        assert resumed.status == "completed", resumed.errors
        assert resumed.raw_data == first.raw_data
        stored_series = [
            series
            for (state_json,) in database.connection.execute(
                "SELECT state_json FROM node_checkpoints"
            )
            for series in json.loads(state_json).get("raw_data", {}).values()
        ]
        assert stored_series
        assert all(isinstance(series, dict) and "$candles" in series for series in stored_series)
        series_rows = database.connection.execute("SELECT COUNT(*) FROM candle_series")
        distinct = {
            json.dumps([bar.model_dump(mode="json") for bar in bars])
            for bars in first.raw_data.values()
        }
        assert series_rows.fetchone()[0] == len(distinct)  # identical series stored once
    finally:
        framework.close()
        database.close()


def test_critic_cycle_stops_after_three_total_strategy_attempts(candles) -> None:
    framework, database, _, llm, execution = _framework(
        candles,
        max_revision_loops=2,
        min_backtest_net_pnl=1_000_000,
    )
    try:
        result = framework.run("BTC/USDT")
        assert result.status == "rejected"
        assert (
            database.connection.execute(
                "SELECT status FROM runs WHERE run_id=?", (result.run_id,)
            ).fetchone()["status"]
            == "rejected"
        )
        assert llm.calls["submit_strategy"] == 3
        assert llm.calls["submit_critique"] == 3
        assert execution.dry_run_values == []
        assert "3 total attempts" in result.errors[-1]
        versions = database.connection.execute(
            "SELECT version FROM strategies WHERE run_id=? ORDER BY version",
            (result.run_id,),
        ).fetchall()
        assert [row["version"] for row in versions] == [0, 1, 2]
    finally:
        framework.close()
        database.close()


def test_run_config_sets_its_own_attempt_limit(candles) -> None:
    framework, database, _, llm, _execution = _framework(
        candles,
        max_revision_loops=2,
        min_backtest_net_pnl=1_000_000,
    )
    try:
        result = framework.run("BTC/USDT", {"max_revision_loops": 0})
        assert result.status == "rejected"
        assert llm.calls["submit_strategy"] == 1
        assert "1 total attempts" in result.errors[-1]
        with pytest.raises(ValueError, match="max_revision_loops"):
            framework.run("BTC/USDT", {"max_revision_loops": 21})
    finally:
        framework.close()
        database.close()


def test_rejected_run_continues_with_extra_attempts(candles) -> None:
    framework, database, _, llm, _execution = _framework(
        candles,
        max_revision_loops=0,
        min_backtest_net_pnl=1_000_000,
    )
    try:
        first = framework.run("BTC/USDT")
        assert first.status == "rejected"
        with pytest.raises(ValueError, match="extra strategy attempts"):
            framework.resume(first.run_id)

        continued = framework.resume(first.run_id, extra_attempts=2)

        assert continued.status == "rejected"
        assert llm.calls["submit_strategy"] == 3
        assert continued.revision_count == 2
        assert sum("attempt limit" in error for error in continued.errors) == 1
        assert "3 total attempts" in continued.errors[-1]
    finally:
        framework.close()
        database.close()


def test_insufficient_history_stops_before_strategy_model_call() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    short_candles = [
        {
            "timestamp": start + timedelta(hours=index),
            "open": 100 + index * 0.1,
            "high": 101 + index * 0.1,
            "low": 99 + index * 0.1,
            "close": 100 + index * 0.1,
            "volume": 10,
        }
        for index in range(40)
    ]
    from framework.schemas.models import OHLCVBar

    framework, database, _, llm, _ = _framework(
        [OHLCVBar.model_validate(item) for item in short_candles]
    )
    try:
        result = framework.run("BTC/USDT")
        assert result.status == "failed"
        assert result.strategy is None
        assert "timeframe_coverage" in result.strategy_diagnostics
        assert "100" in result.errors[-1]
        assert llm.calls.get("submit_strategy", 0) == 0
    finally:
        framework.close()
        database.close()


def test_run_many_executes_symbols_concurrently_and_preserves_order(candles) -> None:
    framework, database, _, _, _ = _framework(candles)
    barrier = threading.Barrier(2)

    def fake_run(symbol: str, config: dict[str, Any] | None):
        barrier.wait(timeout=2)
        from framework.core.state import GraphState

        return GraphState(
            run_id=symbol,
            symbol=symbol,
            timeframes_to_consider=["1h"],
            status="completed",
        )

    framework._run_isolated_symbol = fake_run
    try:
        results = framework.run_many(["BTC/USDT", "ETH/USDT"], max_workers=2)
        assert [result.symbol for result in results] == ["BTC/USDT", "ETH/USDT"]
    finally:
        framework.close()
        database.close()


def test_run_many_processes_live_symbols_sequentially(candles) -> None:
    framework, database, _, _, _ = _framework(candles)
    framework.settings.dry_run = False
    framework.settings.kill_switch = False
    calls: list[str] = []

    def fake_run(symbol: str, config: dict[str, Any] | None):
        calls.append(symbol)
        from framework.core.state import GraphState

        return GraphState(
            run_id=symbol,
            symbol=symbol,
            timeframes_to_consider=["1h"],
            status="completed",
        )

    framework._run_isolated_symbol = fake_run
    try:
        results = framework.run_many(["BTC/USDT", "ETH/USDT", "SOL/USDT"], max_workers=3)
        assert calls == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        assert [result.symbol for result in results] == calls
    finally:
        framework.close()
        database.close()


def test_stopped_run_can_resume_from_the_latest_checkpoint(candles) -> None:
    stop_event = threading.Event()
    stop_event.set()
    framework, database, _, _, _ = _framework(candles, stop_event=stop_event)
    try:
        stopped = framework.run("BTC/USDT", run_id="stopped-run")
        assert stopped.status == "stopped"
        assert database.resumable_run("stopped-run")["status"] == "stopped"

        stop_event.clear()
        resumed = framework.resume("stopped-run", account_equity=5000)
        assert resumed.status == "completed", resumed.errors
        assert resumed.account_equity == 5000
    finally:
        framework.close()
        database.close()
