from pathlib import Path
from types import SimpleNamespace

from framework.agents.critique import CritiqueNode
from framework.config.settings import Settings
from framework.core.state import GraphState
from framework.llm.client import LLMResponse, ToolCall
from framework.persistence.database import Database
from framework.schemas.models import BacktestReport, Critique, StrategyDefinition


class ApprovingLLM:
    def invoke_with_tools(self, messages, tools, model_config):
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id="critique",
                    name="submit_critique",
                    arguments=Critique(
                        decision="APPROVE", rationale="Model recommends approval."
                    ).model_dump(mode="json"),
                )
            ]
        )


def test_critique_cannot_approve_failed_net_cost_thresholds() -> None:
    settings = Settings(
        database_path=Path(":memory:"),
        min_backtest_trades=2,
        min_backtest_net_pnl=0,
        max_backtest_drawdown=0.2,
    )
    database = Database(":memory:")
    database.create_run("run", "BTC/USDT", {})
    strategy = StrategyDefinition(
        name="test",
        description="test",
        chosen_timeframe="1h",
        entry_rule="ema_crossover",
        exit_rule="ema_crossover",
    )
    database.save_strategy("run-v0", "run", strategy.model_dump(mode="json"), 0)
    report = BacktestReport(
        initial_capital=10_000,
        final_equity=9_900,
        net_pnl=-100,
        net_return_pct=-0.01,
        max_drawdown_pct=0.3,
        sharpe_ratio=-1,
        win_rate=0,
        total_trades=0,
        total_fees=5,
        total_slippage=5,
        equity_curve=[],
        trades=[],
        metrics={},
    )
    database.save_backtest("run-v0", "run-v0", {}, [], {})
    context = SimpleNamespace(
        settings=settings,
        database=database,
        llm=ApprovingLLM(),
    )
    state = GraphState(
        run_id="run",
        symbol="BTC/USDT",
        timeframes_to_consider=["1h"],
        strategy=strategy,
        backtest_report=report,
    )
    result = CritiqueNode(context)(state)
    assert result.updates["critique"].decision == "REJECT"
    assert result.updates["revision_count"] == 1
    assert len(result.updates["critique"].issues) >= 3
    database.close()


def test_critique_stops_when_revision_limit_is_zero() -> None:
    settings = Settings(
        database_path=Path(":memory:"),
        min_backtest_trades=1,
        max_revision_loops=0,
    )
    database = Database(":memory:")
    database.create_run("run", "BTC/USDT", {})
    strategy = StrategyDefinition(
        name="test",
        description="test",
        chosen_timeframe="1h",
        entry_rule="ema_crossover",
        exit_rule="ema_crossover",
    )
    database.save_strategy("run-v0", "run", strategy.model_dump(mode="json"), 0)
    report = BacktestReport(
        initial_capital=10_000,
        final_equity=9_900,
        net_pnl=-100,
        net_return_pct=-0.01,
        max_drawdown_pct=0,
        sharpe_ratio=-1,
        win_rate=0,
        total_trades=0,
        total_fees=5,
        total_slippage=5,
        equity_curve=[],
        trades=[],
        metrics={},
    )
    database.save_backtest("run-v0", "run-v0", {}, [], {})
    context = SimpleNamespace(settings=settings, database=database, llm=ApprovingLLM())
    state = GraphState(
        run_id="run",
        symbol="BTC/USDT",
        timeframes_to_consider=["1h"],
        strategy=strategy,
        backtest_report=report,
    )
    result = CritiqueNode(context)(state)
    assert result.updates["status"] == "failed"
    assert result.updates.get("revision_count", state.revision_count) == 0
    database.close()
