from collections.abc import Sequence
from datetime import UTC, datetime

from framework.schemas.models import OHLCVBar, StrategyDefinition
from framework.strategies.signals import LIVE_SIGNAL_BARS
from framework.tools.candle_store import candle_open, timeframe_ms
from framework.tools.market_data import MarketDataTools
from framework.tools.markets import perpetual_symbol


def fetch_strategy_ohlcv(
    market_data: MarketDataTools,
    symbol: str,
    strategy: StrategyDefinition,
    *,
    limit: int = LIVE_SIGNAL_BARS,
    now: datetime | None = None,
) -> dict[str, Sequence[OHLCVBar]]:
    """The closed candles a live strategy decides on, ``limit`` per timeframe.

    The exchange also returns the candle that is still forming. It is dropped: the
    backtest decides on each candle once it has closed, and a forming candle's signal
    changes with every tick until it does.
    """
    now = now or datetime.now(UTC)
    timeframes = {strategy.chosen_timeframe}
    higher_timeframe = strategy.parameters.get("higher_timeframe")
    if isinstance(higher_timeframe, str):
        timeframes.add(higher_timeframe)
    data = {
        timeframe: closed_candles(
            market_data.fetch_ohlcv(symbol, timeframe, limit=limit + 1).candles, timeframe, now
        )[-limit:]
        for timeframe in sorted(timeframes)
    }
    pair_symbol = strategy.parameters.get("pair_symbol")
    if isinstance(pair_symbol, str) and strategy.template_name == "stat_arb_spread":
        data["pair"] = closed_candles(
            market_data.fetch_ohlcv(
                pair_symbol, strategy.chosen_timeframe, limit=limit + 1
            ).candles,
            strategy.chosen_timeframe,
            now,
        )[-limit:]
    if strategy.template_name == "carry_funding":
        funding = getattr(market_data, "fetch_funding_history", None)
        if funding is not None:
            data["funding"] = funding(perpetual_symbol(symbol))
    return data


def closed_candles(candles: Sequence[OHLCVBar], timeframe: str, now: datetime) -> list[OHLCVBar]:
    """``candles`` without any whose period has not ended by ``now``."""
    length = timeframe_ms(timeframe)
    if length is None:
        return list(candles)
    now_ms = now.timestamp() * 1000
    return [candle for candle in candles if candle.timestamp.timestamp() * 1000 + length <= now_ms]


def next_close(timeframe: str, now: datetime) -> datetime | None:
    """When the candle forming at ``now`` closes; None for calendar timeframes."""
    length = timeframe_ms(timeframe)
    if length is None:
        return None
    opened = candle_open(int(now.timestamp() * 1000), timeframe)
    return datetime.fromtimestamp((opened + length) / 1000, UTC)
