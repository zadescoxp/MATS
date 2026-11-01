from datetime import UTC, datetime
from typing import Any

import ccxt
from pydantic import BaseModel

from framework.config.settings import Settings
from framework.schemas.models import OHLCVBar


class OHLCVResult(BaseModel):
    symbol: str
    timeframe: str
    candles: list[OHLCVBar]


class TickerSnapshot(BaseModel):
    symbol: str
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    base_volume: float | None = None
    quote_volume: float | None = None
    timestamp: int | None = None


class OrderBookSnapshot(BaseModel):
    symbol: str
    bids: list[list[float]]
    asks: list[list[float]]
    timestamp: int | None = None


class MarketDataTools:
    def __init__(self, settings: Settings, exchange: Any | None = None):
        self.settings = settings
        self.exchange = exchange or self._make_exchange(settings)

    @staticmethod
    def _make_exchange(settings: Settings) -> Any:
        exchange = ccxt.bybit(
            {
                "enableRateLimit": True,
                "timeout": settings.request_timeout_ms,
                "rateLimit": settings.exchange_rate_limit_ms,
            }
        )
        exchange.set_sandbox_mode(settings.market_data_testnet)
        return exchange

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> OHLCVResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        candles = [OHLCVBar.from_ccxt(row) for row in rows]
        if any(
            left.timestamp >= right.timestamp
            for left, right in zip(candles, candles[1:], strict=False)
        ):
            raise ValueError("Exchange returned OHLCV candles that are not strictly time-ordered")
        return OHLCVResult(symbol=symbol, timeframe=timeframe, candles=candles)

    def fetch_realtime_ticker(self, symbol: str) -> TickerSnapshot:
        ticker = self.exchange.fetch_ticker(symbol)
        return TickerSnapshot(
            symbol=symbol,
            last=ticker.get("last"),
            bid=ticker.get("bid"),
            ask=ticker.get("ask"),
            base_volume=ticker.get("baseVolume"),
            quote_volume=ticker.get("quoteVolume"),
            timestamp=ticker.get("timestamp"),
        )

    def fetch_orderbook(self, symbol: str, limit: int = 50) -> OrderBookSnapshot:
        if limit <= 0:
            raise ValueError("limit must be positive")
        book = self.exchange.fetch_order_book(symbol, limit=limit)
        return OrderBookSnapshot(
            symbol=symbol,
            bids=book.get("bids", []),
            asks=book.get("asks", []),
            timestamp=book.get("timestamp"),
        )

    def fetch_funding_history(self, symbol: str, limit: int = 200) -> list[OHLCVBar]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not hasattr(self.exchange, "fetch_funding_rate_history"):
            return []
        try:
            rows = self.exchange.fetch_funding_rate_history(symbol, limit=limit)
        except Exception:
            return []
        candles: list[OHLCVBar] = []
        for row in rows:
            rate = row.get("fundingRate")
            timestamp = row.get("timestamp")
            if not isinstance(rate, int | float) or not isinstance(timestamp, int | float):
                continue
            if rate <= -1:
                continue
            level = 1 + float(rate)
            moment = datetime.fromtimestamp(float(timestamp) / 1000, tz=UTC)
            candles.append(
                OHLCVBar(
                    timestamp=moment,
                    open=level,
                    high=level,
                    low=level,
                    close=level,
                    volume=0,
                )
            )
        return candles

    def fetch_recent_trades(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        trades = self.exchange.fetch_trades(symbol, limit=limit)
        return [
            {
                "id": trade.get("id"),
                "timestamp": trade.get("timestamp"),
                "side": trade.get("side"),
                "price": trade.get("price"),
                "amount": trade.get("amount"),
                "cost": trade.get("cost"),
            }
            for trade in trades
        ]
