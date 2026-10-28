from typing import Any

from framework.core.context import AgentContext
from framework.core.state import GraphState


class DataWorkerNode:
    def __init__(self, context: AgentContext, candle_limit: int = 500):
        self.context = context
        self.candle_limit = candle_limit

    def __call__(self, state: GraphState) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for timeframe in state.timeframes_to_consider:
            result = self.context.market_data.fetch_ohlcv(
                state.symbol, timeframe, limit=self.candle_limit
            )
            data[timeframe] = result.candles

        ticker = self.context.market_data.fetch_realtime_ticker(state.symbol)
        market_context: dict[str, Any] = {"ticker": ticker.model_dump(mode="json")}
        if self.context.settings.include_orderbook:
            book = self.context.market_data.fetch_orderbook(state.symbol)
            market_context["orderbook"] = book.model_dump(mode="json")
        if self.context.settings.include_recent_trades:
            market_context["recent_trades"] = self.context.market_data.fetch_recent_trades(
                state.symbol
            )
        return {"raw_data": data, "market_context": market_context}
