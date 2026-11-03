"""Paper-trade an approved strategy on candles that closed after it was approved.

Backtest signals are the live signals, so replaying the strategy over candles it has
never seen is exactly what paper trading it would have done. An algorithm only places
live orders once this forward test has closed enough trades at a profit.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from framework.schemas.models import StrategyDefinition
from framework.strategies.market_data import fetch_strategy_ohlcv
from framework.strategies.signals import LIVE_SIGNAL_BARS
from framework.tools.backtest import BacktestTool
from framework.tools.candle_store import timeframe_ms
from framework.tools.market_data import MarketDataTools

_MAX_BARS = 20_000


@dataclass
class ForwardTest:
    verdict: Literal["pending", "passed", "failed"]
    closed_trades: int
    net_pnl: float
    bars: int
    detail: str


def forward_test(
    backtester: BacktestTool,
    market_data: MarketDataTools,
    symbol: str,
    strategy: StrategyDefinition,
    approved_at: datetime,
    required_trades: int,
    capital: float,
    now: datetime | None = None,
) -> ForwardTest:
    now = now or datetime.now(UTC)
    length = timeframe_ms(strategy.chosen_timeframe) or 60_000
    elapsed = max(0, math.ceil((now - approved_at).total_seconds() * 1000 / length))
    limit = min(elapsed + LIVE_SIGNAL_BARS + 2, _MAX_BARS)
    data = fetch_strategy_ohlcv(market_data, symbol, strategy, limit=limit, now=now)
    # Only closed candles: the one still forming could reverse before it closes.
    candles = [
        candle
        for candle in data[strategy.chosen_timeframe]
        if candle.timestamp.timestamp() * 1000 + length <= now.timestamp() * 1000
    ]
    start = next(
        (index for index, candle in enumerate(candles) if candle.timestamp > approved_at), None
    )
    if start is None or len(candles) - start < 2:
        return ForwardTest("pending", 0, 0.0, 0, "Waiting for candles that closed after approval.")
    signals = backtester.signals(strategy, candles, data=data)
    report = backtester.simulate(strategy, candles[start:], signals[start:], capital)
    closed = [trade for trade in report.trades if trade.exit_reason != "end_of_data"]
    net_pnl = sum(trade.net_pnl for trade in closed)
    bars = len(candles) - start
    summary = (
        f"{len(closed)} of {required_trades} paper trades closed over {bars} new "
        f"{strategy.chosen_timeframe} candles, net {net_pnl:+.2f} after costs."
    )
    if len(closed) < required_trades:
        return ForwardTest("pending", len(closed), net_pnl, bars, f"Forward test: {summary}")
    if net_pnl > 0:
        return ForwardTest("passed", len(closed), net_pnl, bars, f"Forward test passed: {summary}")
    return ForwardTest(
        "failed",
        len(closed),
        net_pnl,
        bars,
        f"Forward test failed: {summary} The backtested edge did not hold on new data.",
    )
