from datetime import UTC, datetime

import pytest

from framework.schemas.models import OHLCVBar, StrategyDefinition, TradeRecord
from framework.tools.backtest import BacktestTool
from framework.tools.cost_model import CostAssumptions, reprice_trade_list


def test_reprice_applies_entry_and_exit_fee_and_slippage() -> None:
    trade = TradeRecord(
        entry_time=datetime(2024, 1, 1, tzinfo=UTC),
        exit_time=datetime(2024, 1, 2, tzinfo=UTC),
        entry_price=100,
        exit_price=110,
        quantity=1,
        entry_notional=100,
        exit_notional=110,
        gross_pnl=10,
        fees=0,
        slippage=0,
        net_pnl=10,
        exit_reason="test",
    )
    repriced = reprice_trade_list(
        [trade],
        CostAssumptions(
            maker_fee_rate=0.001,
            taker_fee_rate=0.002,
            slippage_percent=0.001,
            slippage_fixed=0.5,
        ),
    )[0]
    assert repriced.fees == 0.42
    assert repriced.slippage == 1.21
    assert repriced.net_pnl == pytest.approx(8.37)


def test_backtest_costs_reduce_equity(candles: list[OHLCVBar]) -> None:
    strategy = StrategyDefinition(
        name="rising trend",
        description="Short EMA crossover test",
        chosen_timeframe="1h",
        entry_rule="ema_crossover",
        exit_rule="ema_crossover",
        fast_period=2,
        slow_period=5,
        stop_loss_pct=0.2,
        position_fraction=0.5,
    )
    no_costs = BacktestTool(CostAssumptions(), initial_capital=10_000)
    paid_costs = BacktestTool(
        CostAssumptions(
            maker_fee_rate=0.01,
            taker_fee_rate=0.01,
            slippage_percent=0.01,
        ),
        initial_capital=10_000,
    )
    free_report = no_costs.run(strategy, candles)
    paid_report = paid_costs.run(strategy, candles)
    assert free_report.total_trades > 0
    assert paid_report.final_equity < free_report.final_equity
    assert paid_report.total_fees > 0
    assert paid_report.total_slippage > 0


def test_run_uses_requested_capital_instead_of_the_tool_default(candles: list[OHLCVBar]) -> None:
    strategy = StrategyDefinition(
        name="rising trend",
        description="Short EMA crossover test",
        chosen_timeframe="1h",
        entry_rule="ema_crossover",
        exit_rule="ema_crossover",
        fast_period=2,
        slow_period=5,
        stop_loss_pct=0.2,
        position_fraction=0.5,
    )
    tool = BacktestTool(CostAssumptions(), initial_capital=10_000)
    default_report = tool.run(strategy, candles)
    sized_report = tool.run(strategy, candles, initial_capital=25_000)
    assert default_report.initial_capital == 10_000
    assert sized_report.initial_capital == 25_000
    assert sized_report.final_equity == pytest.approx(default_report.final_equity * 2.5)
