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
