import logging
from time import perf_counter
from typing import Any

from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.tools.context_feed import collect_context, reference_pair

logger = logging.getLogger(__name__)


class DataWorkerNode:
    def __init__(self, context: AgentContext, candle_limit: int = 500):
        self.context = context
        self.candle_limit = candle_limit

    def __call__(self, state: GraphState) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for timeframe in state.timeframes_to_consider:
            started_at = perf_counter()
            result = self.context.market_data.fetch_ohlcv(
                state.symbol, timeframe, limit=self.candle_limit
            )
            data[timeframe] = result.candles
            logger.info(
                "Primary market data ready (timeframe=%s, candles=%d, duration=%.2fs).",
                timeframe,
                len(result.candles),
                perf_counter() - started_at,
            )

        ticker = self.context.market_data.fetch_realtime_ticker(state.symbol)
        market_context: dict[str, Any] = {"ticker": ticker.model_dump(mode="json")}
        if self.context.settings.include_external_context:
            try:
                market_context["external"] = collect_context(state.symbol)
            except Exception:
                logger.warning(
                    "External context collection failed; continuing without it.",
                    exc_info=True,
                )
                market_context["external"] = {
                    "items": [],
                    "short_horizon": [],
                    "long_horizon": [],
                    "unavailable_sources": ["context_feed"],
                }
        if self.context.settings.include_pair_context:
            pair = reference_pair(state.symbol)
            if pair:
                pair_candles: dict[str, Any] = {}
                for timeframe in state.timeframes_to_consider:
                    try:
                        candles = self.context.market_data.fetch_ohlcv(
                            pair, timeframe, limit=self.candle_limit
                        ).candles
                        pair_candles[timeframe] = [
                            candle.model_dump(mode="json") for candle in candles
                        ]
                    except Exception:
                        logger.warning(
                            "Reference-pair candles unavailable (pair=%s, timeframe=%s).",
                            pair,
                            timeframe,
                            exc_info=True,
                        )
                        continue
                if pair_candles:
                    market_context["pair_symbol"] = pair
                    market_context["pair_candles"] = pair_candles
        fetch_funding = getattr(self.context.market_data, "fetch_funding_history", None)
        if fetch_funding is not None:
            try:
                funding = fetch_funding(state.symbol)
            except Exception:
                logger.warning("Funding history unavailable; continuing without it.", exc_info=True)
                funding = []
            if funding:
                market_context["funding_history"] = funding
        if self.context.settings.include_orderbook:
            book = self.context.market_data.fetch_orderbook(state.symbol)
            market_context["orderbook"] = book.model_dump(mode="json")
        if self.context.settings.include_recent_trades:
            market_context["recent_trades"] = self.context.market_data.fetch_recent_trades(
                state.symbol
            )
        return {"raw_data": data, "market_context": market_context}
