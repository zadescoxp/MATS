"""Testnet test mode: trades without an edge, kept apart from real strategy history."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from framework.agents.critique import CritiqueNode
from framework.agents.execution import ExecutionNode
from framework.agents.strategy_builder import _shortlist
from framework.config.settings import Settings
from framework.core.state import GraphState
from framework.llm.client import LLMResponse, ToolCall
from framework.persistence.database import Database
from framework.schemas.models import BacktestReport, Critique, StrategyDefinition
from framework.strategies.library import is_test_mode_key, live_strategy_key
from framework.web import DashboardService

STRATEGY = StrategyDefinition(
    name="test",
    description="test",
    template_name="supertrend_atr",
    chosen_timeframe="5m",
    entry_rule="supertrend_atr",
    exit_rule="opposite_signal",
)


class RejectingLLM:
    def invoke_with_tools(self, messages, tools, model_config):
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id="critique",
                    name="submit_critique",
                    arguments=Critique(
                        decision="REJECT", issues=["Costs eat the edge."], rationale="Loses."
                    ).model_dump(mode="json"),
                )
            ]
        )


def _losing_review(test_mode: bool):
    settings = Settings(database_path=Path(":memory:"), min_backtest_trades=2)
    database = Database(":memory:")
    database.create_run("run", "BTC/USDT:USDT", {})
    database.save_strategy("run-v0", "run", STRATEGY.model_dump(mode="json"), 0)
    database.save_backtest("run-v0", "run-v0", {}, [], {})
    segment = {"net_pnl": -50.0, "trades": 20}
    report = BacktestReport(
        initial_capital=10_000,
        final_equity=9_900,
        net_pnl=-100,
        net_return_pct=-0.01,
        max_drawdown_pct=0.02,
        sharpe_ratio=-1.0,
        win_rate=0.4,
        total_trades=40,
        total_fees=80,
        total_slippage=20,
        equity_curve=[],
        trades=[],
        metrics={"cost_edge_ratio": 0.5},
        walk_forward={"split": 0.7, "in_sample": segment, "out_of_sample": segment},
    )
    state = GraphState(
        run_id="run",
        symbol="BTC/USDT:USDT",
        timeframes_to_consider=["5m"],
        strategy=STRATEGY,
        backtest_report=report,
        regime={"regime": "choppy_normal_vol"},
        test_mode=test_mode,
    )
    context = SimpleNamespace(settings=settings, database=database, llm=RejectingLLM())
    result = CritiqueNode(context)(state)
    memory = database.recent_family_results("BTC/USDT:USDT", "choppy_normal_vol")
    database.close()
    return result.updates["critique"], memory


def test_test_mode_approves_a_losing_strategy_and_keeps_it_out_of_family_memory() -> None:
    critique, memory = _losing_review(test_mode=True)

    assert critique.decision == "APPROVE"
    assert critique.issues and all(
        issue.startswith("Test mode (not blocking): ") for issue in critique.issues
    )
    assert memory == []


def test_without_test_mode_the_same_strategy_is_rejected_and_remembered() -> None:
    critique, memory = _losing_review(test_mode=False)

    assert critique.decision == "REJECT"
    assert len(memory) == 1


def test_test_mode_shortlists_the_most_active_candidates() -> None:
    candidates = [
        {"strategy": STRATEGY, "net_pnl": 50.0, "max_drawdown": 0.01, "trades": 5},
        {"strategy": STRATEGY, "net_pnl": -80.0, "max_drawdown": 0.03, "trades": 90},
    ]

    assert _shortlist(candidates)[0]["trades"] == 5
    assert _shortlist(candidates, most_active=True)[0]["trades"] == 90


def test_test_mode_keys_are_kept_apart_from_real_strategy_keys() -> None:
    assert live_strategy_key("abc", False) == "abc"
    assert live_strategy_key("abc", True) == "test:abc"
    assert is_test_mode_key("test:abc") and not is_test_mode_key("abc")


def test_test_mode_is_refused_off_the_testnet(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "state.db", bybit_testnet=False)
    service = DashboardService(settings)

    with pytest.raises(ValueError, match="testnet"):
        service.start({"symbols": ["BTC/USDT:USDT"], "timeframes": ["5m"], "test_mode": True})
    with pytest.raises(ValueError, match="boolean"):
        DashboardService(Settings(database_path=tmp_path / "other.db")).start(
            {"symbols": ["BTC/USDT"], "test_mode": "yes"}
        )


def test_execution_refuses_a_test_mode_strategy_off_the_testnet() -> None:
    settings = Settings(database_path=Path(":memory:"), bybit_testnet=False)
    database = SimpleNamespace(log_event=lambda *_args, **_kwargs: None)
    state = GraphState.model_construct(
        run_id="run",
        symbol="BTC/USDT:USDT",
        timeframes_to_consider=["5m"],
        strategy=STRATEGY,
        critique=Critique(decision="APPROVE", rationale="test"),
        risk_assessment=SimpleNamespace(approved=True, position_size=1),
        test_mode=True,
    )

    context = SimpleNamespace(settings=settings, database=database)
    with pytest.raises(ValueError, match="testnet"):
        ExecutionNode(context)(state)
