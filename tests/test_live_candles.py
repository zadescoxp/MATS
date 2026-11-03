"""Live strategies decide on closed candles only, at the close, as the backtest does."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from framework.schemas.models import LiveSignal, OHLCVBar, StrategyDefinition
from framework.strategies.market_data import closed_candles, fetch_strategy_ohlcv, next_close
from framework.tools import backtest as backtest_module
from framework.tools.backtest import BacktestTool
from framework.tools.cost_model import CostAssumptions
from framework.tools.market_data import OHLCVResult
from framework.web import _closed_bar_key, _next_check_wait

START = datetime(2026, 11, 3, tzinfo=UTC)
COSTS = CostAssumptions(taker_fee_rate=0.001, maker_fee_rate=0.001, slippage_percent=0.0005)


def _bars(count: int, hours: int = 1, start: datetime = START) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=start + timedelta(hours=hours * index),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1,
        )
        for index in range(count)
    ]


def _strategy(**update) -> StrategyDefinition:
    fields = {
        "name": "probe",
        "description": "probe",
        "chosen_timeframe": "1h",
        "template_name": "multi_timeframe_hybrid",
        "entry_rule": "multi_timeframe_hybrid",
        "exit_rule": "fixed_risk_reward",
        "parameters": {"higher_timeframe": "4h"},
        "stop_loss_pct": 0.015,
        "take_profit_pct": 0.02,
    }
    return StrategyDefinition(**{**fields, **update})


class FakeMarket:
    """Serves candles up to and including the one forming at ``now``, like Bybit."""

    def __init__(self, now: datetime):
        self.now = now
        self.limits: list[int] = []

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> OHLCVResult:
        self.limits.append(limit)
        hours = 4 if timeframe == "4h" else 1
        count = int((self.now - START) / timedelta(hours=hours)) + 1
        candles = _bars(count, hours)[-limit:]
        return OHLCVResult(symbol=symbol, timeframe=timeframe, candles=candles)


def test_closed_candles_drop_the_forming_candle() -> None:
    bars = _bars(3)  # 00:00, 01:00, 02:00
    at = START + timedelta(hours=2, minutes=30)
    assert [bar.timestamp.hour for bar in closed_candles(bars, "1h", at)] == [0, 1]
    # The 02:00 candle counts as closed from 03:00 exactly.
    assert len(closed_candles(bars, "1h", START + timedelta(hours=3))) == 3


def test_strategy_candles_end_with_the_last_closed_candle_and_keep_the_window() -> None:
    now = START + timedelta(hours=9, minutes=1)
    market = FakeMarket(now)

    data = fetch_strategy_ohlcv(market, "ETH/USDT", _strategy(), limit=5, now=now)

    assert data["1h"][-1].timestamp == START + timedelta(hours=8)
    assert len(data["1h"]) == 5
    # The 08:00 4h candle is still forming at 09:01, so the last closed one is 04:00.
    assert data["4h"][-1].timestamp == START + timedelta(hours=4)
    assert market.limits == [6, 6]  # one extra, for the forming candle that is dropped


def test_next_close_and_check_wait_land_just_after_the_close() -> None:
    state = SimpleNamespace(strategy=SimpleNamespace(chosen_timeframe="1h"))
    now = START + timedelta(hours=5, minutes=59, seconds=30)

    assert next_close("1h", now) == START + timedelta(hours=6)
    assert next_close("4h", now) == START + timedelta(hours=8)
    # 30 s to the close plus a few seconds for Bybit to publish the closed candle.
    assert _next_check_wait(60, state, now=now) == pytest.approx(35)
    # Mid-candle the usual interval applies.
    assert _next_check_wait(60, state, now=START + timedelta(minutes=10)) == 60
    # A failed check is retried within seconds, not a full interval later.
    assert _next_check_wait(60, state, failed=True, now=START + timedelta(minutes=10)) == 15


def test_bar_key_is_the_close_time_and_a_failed_lookup_raises() -> None:
    now = datetime.now(UTC)
    state = SimpleNamespace(symbol="BTC/USDT", strategy=_strategy(parameters={}))
    worker = SimpleNamespace(context=SimpleNamespace(market_data=FakeMarket(now)))

    key = datetime.fromisoformat(_closed_bar_key(worker, state))
    assert key <= now < key + timedelta(hours=1)

    class Broken:
        def fetch_ohlcv(self, *_args, **_kwargs):
            raise RuntimeError("svc error: Get kline failed")

    worker = SimpleNamespace(context=SimpleNamespace(market_data=Broken()))
    with pytest.raises(RuntimeError, match="kline"):
        _closed_bar_key(worker, state)


def test_backtest_only_shows_higher_timeframe_candles_that_had_closed(monkeypatch) -> None:
    seen: list[tuple[datetime, datetime]] = []

    def record(strategy, data, position=None):
        primary = data["1h"]
        higher = data["4h"]
        seen.append(
            (primary["timestamp"].iloc[-1], higher["timestamp"].iloc[-1] if len(higher) else None)
        )
        return LiveSignal(action="hold", reason="probe", strength=0, price=100)

    monkeypatch.setattr(backtest_module, "evaluate_live_signal", record)
    hourly = _bars(48)
    four_hourly = _bars(12, hours=4)

    BacktestTool(COSTS).signals(_strategy(), hourly, data={"4h": four_hourly})

    assert seen
    for primary_open, higher_open in seen:
        if higher_open is None:
            continue
        # Decided at the 1h close; the 4h candle must have closed by then too.
        assert _ts(higher_open) + timedelta(hours=4) <= _ts(primary_open) + timedelta(hours=1)
    # At the 03:00 candle (closing 04:00) the 00:00 4h candle has just closed.
    assert any(
        _ts(p) == START + timedelta(hours=3) and _ts(h) == START for p, h in seen if h is not None
    )


def _ts(value) -> datetime:
    stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
