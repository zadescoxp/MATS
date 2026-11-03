import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from framework.agents.critique import CritiqueNode
from framework.config.settings import Settings
from framework.schemas.models import BacktestReport, LiveSignal, OHLCVBar, StrategyDefinition
from framework.strategies.forward_test import forward_test
from framework.strategies.search import TemplateJob, measure_template, risk_variants
from framework.strategies.signals import evaluate_live_signal
from framework.tools.backtest import BacktestTool
from framework.tools.cost_model import CostAssumptions
from framework.tools.market_data import OHLCVResult

START = datetime(2026, 1, 1, tzinfo=UTC)
COSTS = CostAssumptions(taker_fee_rate=0.001, maker_fee_rate=0.001, slippage_percent=0.0005)


def _bars(prices: list[float], start: datetime = START) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=start + timedelta(hours=index),
            open=price,
            high=price * 1.002,
            low=price * 0.998,
            close=price,
            volume=10,
        )
        for index, price in enumerate(prices)
    ]


def _wave(count: int, drift: float = 0.0) -> list[float]:
    return [100 * (1 + drift) ** index + 6 * math.sin(index / 9) for index in range(count)]


def _strategy(**update) -> StrategyDefinition:
    fields = {
        "name": "probe",
        "description": "probe",
        "chosen_timeframe": "1h",
        "template_name": "mean_reversion",
        "entry_rule": "mean_reversion",
        "exit_rule": "fixed_risk_reward",
        "parameters": {"z_period": 20, "z_entry": 1.5, "rsi_period": 2, "rsi_extreme": 10},
        "stop_loss_pct": 0.015,
        "take_profit_pct": 0.02,
    }
    return StrategyDefinition(**{**fields, **update})


def test_walk_forward_splits_the_history_and_measures_costs() -> None:
    candles = _bars(_wave(600))
    report = BacktestTool(COSTS, 10_000, walk_forward_split=0.7).run(_strategy(), candles)

    walk_forward = report.walk_forward
    assert walk_forward is not None
    assert walk_forward["in_sample"]["bars"] == 420
    assert walk_forward["out_of_sample"]["bars"] == 180
    assert walk_forward["out_of_sample"]["start"] == candles[420].timestamp.isoformat()
    paid = report.total_fees + report.total_slippage
    gross = sum(trade.gross_pnl for trade in report.trades)
    assert report.total_trades > 0
    assert math.isclose(report.metrics["cost_edge_ratio"], gross / paid)
    assert math.isclose(report.metrics["buy_and_hold_return"], candles[-1].close / 100 - 1)


def test_short_histories_have_no_walk_forward_split() -> None:
    report = BacktestTool(COSTS).run(_strategy(), _bars(_wave(40)))

    assert report.walk_forward is None


def test_risk_variants_widen_stops_and_time_stops() -> None:
    variants = risk_variants(_strategy(time_stop_bars=10))

    assert {"stop_loss_pct": 0.015, "take_profit_pct": 0.02} in variants
    assert {"stop_loss_pct": 0.03, "take_profit_pct": 0.04} in variants
    assert {"stop_loss_pct": 0.015, "take_profit_pct": 0.02, "time_stop_bars": 20} in variants
    assert risk_variants(_strategy(take_profit_pct=None)) == [
        {"stop_loss_pct": 0.015, "take_profit_pct": None},
        {"stop_loss_pct": 0.03, "take_profit_pct": None},
    ]


def test_parameter_search_never_looks_at_the_out_of_sample_history() -> None:
    prices = _wave(600)
    entries = [
        _strategy(),
        _strategy(parameters={"z_period": 50, "z_entry": 2.0, "rsi_period": 2, "rsi_extreme": 5}),
    ]
    tool = BacktestTool(COSTS, 10_000, walk_forward_split=0.7)

    def measure(candles: list[OHLCVBar]) -> dict:
        return measure_template(
            TemplateJob(tool, entries, candles, {"1h": candles}, 10_000, min_trades=1)
        )

    calm = measure(_bars(prices))
    # Same in-sample history, a crash in the held-out part.
    crashed = measure(_bars(prices[:420] + [price * 0.5 for price in prices[420:]]))

    assert calm["measured_on"] == "in_sample"
    assert calm["variants_tested"] == 8
    assert calm["in_sample"]["bars"] == 420
    assert calm["strategy"] == crashed["strategy"]
    assert calm["net_pnl"] == crashed["net_pnl"]


def test_time_series_momentum_enters_on_a_new_uptrend_and_exits_when_it_turns() -> None:
    strategy = StrategyDefinition(
        name="tsm",
        description="tsm",
        chosen_timeframe="1d",
        template_name="time_series_momentum",
        entry_rule="time_series_momentum",
        parameters={"lookback": 5, "trend_period": 10},
        stop_loss_pct=0.08,
    )
    flat_then_up = [100.0] * 20 + [101.0]
    up_then_down = [100.0 + index for index in range(20)] + [100.0]

    assert evaluate_live_signal(strategy, {"1d": _bars(flat_then_up)}).action == "long"
    assert evaluate_live_signal(strategy, {"1d": _bars(up_then_down)}).action == "short"
    rising = [100.0 + index for index in range(21)]
    assert evaluate_live_signal(strategy, {"1d": _bars(rising)}).action == "hold"


def _critique_issues(report: BacktestReport, **settings) -> list[str]:
    node = CritiqueNode(
        SimpleNamespace(settings=Settings(database_path=Path(":memory:"), **settings))
    )
    return node._edge_issues(report)


def _report(**update) -> BacktestReport:
    fields = {
        "initial_capital": 10_000,
        "final_equity": 10_100,
        "net_pnl": 100,
        "net_return_pct": 0.01,
        "max_drawdown_pct": 0.02,
        "sharpe_ratio": 1,
        "win_rate": 0.5,
        "total_trades": 10,
        "total_fees": 20,
        "total_slippage": 10,
        "equity_curve": [],
        "trades": [],
        "metrics": {"cost_edge_ratio": 4.3},
        "walk_forward": {
            "in_sample": {"net_pnl": 80, "trades": 7},
            "out_of_sample": {"net_pnl": 20, "trades": 3},
        },
    }
    return BacktestReport(**{**fields, **update})


def test_critique_requires_profit_on_unseen_history_and_after_costs() -> None:
    assert _critique_issues(_report()) == []

    thin = _critique_issues(_report(metrics={"cost_edge_ratio": 1.2}))
    assert len(thin) == 1 and "1.20x the fees and slippage" in thin[0]

    lost = _critique_issues(
        _report(
            walk_forward={
                "in_sample": {"net_pnl": 150, "trades": 7},
                "out_of_sample": {"net_pnl": -50, "trades": 3},
            }
        )
    )
    assert len(lost) == 1 and "did not hold on unseen history" in lost[0]

    idle = _critique_issues(
        _report(
            walk_forward={
                "in_sample": {"net_pnl": 100, "trades": 10},
                "out_of_sample": {"net_pnl": 0, "trades": 0},
            }
        )
    )
    assert "No trades on the out-of-sample history" in idle[0]
    assert "too short" in _critique_issues(_report(walk_forward=None))[0]
    assert _critique_issues(_report(walk_forward=None), require_out_of_sample_edge=False) == []


class ScriptedBacktester(BacktestTool):
    """Buys every tenth bar and sells five bars later, whatever the prices."""

    def signals(self, strategy, candles, *, data=None):
        return [
            LiveSignal(
                action="long" if index % 10 == 0 else "short" if index % 10 == 5 else "hold",
                reason="script",
                strength=0,
                price=candle.close,
            )
            for index, candle in enumerate(candles)
        ]


class FakeMarketData:
    def __init__(self, candles: list[OHLCVBar]) -> None:
        self.candles = candles
        self.limits: list[int] = []

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> OHLCVResult:
        self.limits.append(limit)
        return OHLCVResult(symbol=symbol, timeframe=timeframe, candles=self.candles[-limit:])


def _forward(prices: list[float], required: int, now_offset: int = 0):
    candles = _bars(prices)
    market = FakeMarketData(candles)
    approved_at = candles[499].timestamp
    now = candles[-1].timestamp + timedelta(hours=1 + now_offset)
    result = forward_test(
        ScriptedBacktester(COSTS),
        market,
        "BTC/USDT",
        _strategy(take_profit_pct=None, stop_loss_pct=0.2),
        approved_at,
        required,
        10_000,
        now=now,
    )
    return result, market


def test_forward_test_paper_trades_only_candles_after_approval() -> None:
    rising = [100.0] * 500 + [100.0 + index for index in range(1, 61)]
    falling = [100.0] * 500 + [100.0 - index * 0.5 for index in range(1, 61)]

    passed, market = _forward(rising, required=3)
    failed, _ = _forward(falling, required=3)
    pending, _ = _forward(rising, required=50)

    assert passed.verdict == "passed" and passed.bars == 60 and passed.net_pnl > 0
    assert passed.closed_trades == 6
    # bars since approval, the live window, and the forming candle that is dropped
    assert market.limits == [61 + 500 + 2 + 1]
    assert failed.verdict == "failed" and failed.net_pnl < 0
    assert pending.verdict == "pending" and pending.closed_trades == 6


def test_forward_test_ignores_the_candle_still_forming() -> None:
    prices = [100.0] * 500 + [101.0]

    result, _ = _forward(prices, required=1, now_offset=-1)

    assert result.verdict == "pending"
    assert result.bars == 0
