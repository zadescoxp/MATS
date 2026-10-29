from collections.abc import Sequence

from framework.schemas.models import OHLCVBar, StrategyDefinition
from framework.tools.market_data import MarketDataTools


def fetch_strategy_ohlcv(
    market_data: MarketDataTools,
    symbol: str,
    strategy: StrategyDefinition,
    *,
    limit: int = 500,
) -> dict[str, Sequence[OHLCVBar]]:
    timeframes = {strategy.chosen_timeframe}
    higher_timeframe = strategy.parameters.get("higher_timeframe")
    if isinstance(higher_timeframe, str):
        timeframes.add(higher_timeframe)
    return {
        timeframe: market_data.fetch_ohlcv(symbol, timeframe, limit=limit).candles
        for timeframe in sorted(timeframes)
    }
