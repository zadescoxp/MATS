import json
from datetime import UTC, datetime
from types import SimpleNamespace

from framework.agents.data_worker import DataWorkerNode
from framework.core.state import GraphState
from framework.schemas.models import OHLCVBar
from framework.tools.market_data import OHLCVResult, TickerSnapshot


def test_data_worker_serializes_pair_candles_for_analyst_context() -> None:
    candle = OHLCVBar(
        timestamp=datetime(2026, 11, 1, tzinfo=UTC),
        open=100,
        high=110,
        low=90,
        close=105,
        volume=12,
    )

    class MarketData:
        def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> OHLCVResult:
            assert symbol in {"BTC/USDT", "ETH/USDT"}
            return OHLCVResult(symbol=symbol, timeframe=timeframe, candles=[candle])

        def fetch_realtime_ticker(self, symbol: str) -> TickerSnapshot:
            return TickerSnapshot(symbol=symbol)

    context = SimpleNamespace(
        settings=SimpleNamespace(
            include_external_context=False,
            include_pair_context=True,
            include_orderbook=False,
            include_recent_trades=False,
            min_history_days=30,
            max_history_bars=5000,
        ),
        market_data=MarketData(),
    )
    state = GraphState(
        run_id="test-run",
        symbol="ETH/USDT",
        timeframes_to_consider=["1h"],
    )

    result = DataWorkerNode(context)(state)
    market_context = result["market_context"]

    assert market_context["pair_candles"]["1h"] == [candle.model_dump(mode="json")]
    json.dumps(market_context)


def test_data_worker_requests_deeper_history_for_short_timeframes() -> None:
    from framework.agents.data_worker import history_bars
    from framework.config.settings import Settings

    candle = OHLCVBar(
        timestamp=datetime(2026, 11, 1, tzinfo=UTC), open=1, high=1, low=1, close=1, volume=1
    )
    requested: list[tuple[str, str, int]] = []

    class MarketData:
        def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> OHLCVResult:
            requested.append((symbol, timeframe, limit))
            return OHLCVResult(symbol=symbol, timeframe=timeframe, candles=[candle])

        def fetch_realtime_ticker(self, symbol: str) -> TickerSnapshot:
            return TickerSnapshot(symbol=symbol)

    settings = Settings(
        include_external_context=False,
        include_pair_context=True,
        min_history_days=30,
        max_history_bars=5000,
    )
    context = SimpleNamespace(settings=settings, market_data=MarketData())
    state = GraphState(run_id="run", symbol="BTC/USDT", timeframes_to_consider=["5m", "1h", "1d"])

    DataWorkerNode(context)(state)

    assert ("BTC/USDT", "5m", 5000) in requested  # 30 days would be 8640; capped
    assert ("BTC/USDT", "1h", 720) in requested  # 30 days of hourly candles
    assert ("BTC/USDT", "1d", 500) in requested  # never fewer than the live window
    assert ("ETH/USDT", "5m", 5000) in requested  # the reference pair gets the same depth
    assert history_bars("15m", settings) == 2880
    assert history_bars("1M", settings) == 500
