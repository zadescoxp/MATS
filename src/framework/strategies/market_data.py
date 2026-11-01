from collections.abc import Sequence

from framework.schemas.models import OHLCVBar, StrategyDefinition
from framework.strategies.signals import LIVE_SIGNAL_BARS
from framework.tools.market_data import MarketDataTools


def fetch_strategy_ohlcv(
    market_data: MarketDataTools,
    symbol: str,
    strategy: StrategyDefinition,
    *,
    limit: int = LIVE_SIGNAL_BARS,
) -> dict[str, Sequence[OHLCVBar]]:
    timeframes = {strategy.chosen_timeframe}
    higher_timeframe = strategy.parameters.get("higher_timeframe")
    if isinstance(higher_timeframe, str):
        timeframes.add(higher_timeframe)
    data = {
        timeframe: market_data.fetch_ohlcv(symbol, timeframe, limit=limit).candles
        for timeframe in sorted(timeframes)
    }
    pair_symbol = strategy.parameters.get("pair_symbol")
    if isinstance(pair_symbol, str) and strategy.template_name == "stat_arb_spread":
        data["pair"] = market_data.fetch_ohlcv(
            pair_symbol, strategy.chosen_timeframe, limit=limit
        ).candles
    if strategy.template_name == "carry_funding":
        funding = getattr(market_data, "fetch_funding_history", None)
        if funding is not None:
            data["funding"] = funding(symbol)
    return data
