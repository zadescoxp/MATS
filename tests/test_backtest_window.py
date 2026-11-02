from datetime import UTC, datetime, timedelta

import framework.tools.backtest as backtest_module
from framework.schemas.models import LiveSignal, OHLCVBar, StrategyDefinition
from framework.strategies.signals import LIVE_SIGNAL_BARS
from framework.tools.backtest import BacktestTool
from framework.tools.cost_model import CostAssumptions

START = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(count: int, step: timedelta) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=START + step * index,
            open=100 + index * 0.01,
            high=101 + index * 0.01,
            low=99 + index * 0.01,
            close=100 + index * 0.01,
            volume=10,
        )
        for index in range(count)
    ]


def _strategy() -> StrategyDefinition:
    return StrategyDefinition(
        name="probe",
        description="probe",
        chosen_timeframe="1h",
        template_name="ema_crossover",
        entry_rule="ema_crossover",
        stop_loss_pct=0.02,
    )


def test_each_bar_sees_the_live_window_and_nothing_from_its_future(monkeypatch) -> None:
    seen: list[dict] = []

    def record(strategy, data, position=None):
        seen.append(
            {
                timeframe: (
                    len(series),
                    series["timestamp"].iloc[-1].to_pydatetime() if len(series) else None,
                )
                for timeframe, series in data.items()
            }
        )
        return LiveSignal(action="hold", reason="probe", strength=0, price=100.0)

    monkeypatch.setattr(backtest_module, "evaluate_live_signal", record)
    hourly = _bars(1200, timedelta(hours=1))
    four_hourly = _bars(400, timedelta(hours=4))
    tool = BacktestTool(CostAssumptions(), 10_000)

    tool.run(_strategy(), hourly, data={"1h": hourly, "4h": four_hourly})

    assert len(seen) == len(hourly) - 1
    for index, view in enumerate(seen, start=1):
        bars_1h, last_1h = view["1h"]
        assert bars_1h == min(index + 1, LIVE_SIGNAL_BARS)
        assert last_1h == hourly[index].timestamp
        bars_4h, last_4h = view["4h"]
        assert bars_4h <= LIVE_SIGNAL_BARS
        assert last_4h is None or last_4h <= hourly[index].timestamp


def test_histories_within_the_live_window_backtest_exactly_as_before(monkeypatch) -> None:
    hourly = [
        OHLCVBar(
            timestamp=START + timedelta(hours=index),
            open=price,
            high=price + 1,
            low=price - 1,
            close=price,
            volume=10,
        )
        for index, price in enumerate(
            100 + 8 * ((index // 25) % 2) - 4 + (index % 25) * 0.3 for index in range(400)
        )
    ]
    tool = BacktestTool(CostAssumptions(), 10_000)
    windowed = tool.run(_strategy(), hourly)

    monkeypatch.setattr(backtest_module, "LIVE_SIGNAL_BARS", 10**9)  # an unlimited prefix
    unlimited = tool.run(_strategy(), hourly)

    assert windowed.total_trades == unlimited.total_trades > 0
    assert windowed.net_pnl == unlimited.net_pnl
    assert [trade.entry_time for trade in windowed.trades] == [
        trade.entry_time for trade in unlimited.trades
    ]


def test_backtest_step_sees_the_higher_timeframe_and_reference_pair() -> None:
    from types import SimpleNamespace

    from framework.agents.backtest import BacktestNode
    from framework.core.state import GraphState

    hourly = _bars(50, timedelta(hours=1))
    four_hourly = _bars(20, timedelta(hours=4))
    seen: dict[str, list] = {}

    class RecordingBacktester:
        def run(self, strategy, candles, *, data=None, initial_capital=None):
            seen.update(data or {})
            return BacktestTool(CostAssumptions(), 10_000).run(strategy, candles, data=data)

    database = SimpleNamespace(save_backtest=lambda *_args: None)
    context = SimpleNamespace(
        settings=SimpleNamespace(
            initial_capital=10_000,
            fee_rate_maker=0.001,
            fee_rate_taker=0.001,
            slippage_percent=0.0,
            slippage_fixed=0.0,
        ),
        backtester=RecordingBacktester(),
        database=database,
    )
    state = GraphState(
        run_id="run",
        symbol="BTC/USDT",
        timeframes_to_consider=["1h", "4h"],
        raw_data={"1h": hourly, "4h": four_hourly},
        market_context={"pair_symbol": "ETH/USDT", "pair_candles": {"1h": hourly}},
        strategy=StrategyDefinition(
            name="pair",
            description="pair",
            chosen_timeframe="1h",
            template_name="stat_arb_spread",
            entry_rule="stat_arb_spread",
            parameters={"z_period": 30, "z_entry": 1.5, "pair_symbol": "ETH/USDT"},
        ),
    )

    BacktestNode(context)(state)

    assert set(seen) == {"1h", "4h", "pair"}
