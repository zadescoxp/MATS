import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import ccxt
from pydantic import BaseModel

from framework.config.settings import Settings
from framework.schemas.models import OHLCVBar

logger = logging.getLogger(__name__)


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
        started_at = perf_counter()
        logger.info("Fetching candles (timeframe=%s, limit=%d).", timeframe, limit)
        try:
            rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception:
            logger.exception("Candle request failed (timeframe=%s).", timeframe)
            raise
        candles = [OHLCVBar.from_ccxt(row) for row in rows]
        if any(
            left.timestamp >= right.timestamp
            for left, right in zip(candles, candles[1:], strict=False)
        ):
            raise ValueError("Exchange returned OHLCV candles that are not strictly time-ordered")
        logger.info(
            "Fetched %d candles in %.2fs (timeframe=%s).",
            len(candles),
            perf_counter() - started_at,
            timeframe,
        )
        return OHLCVResult(symbol=symbol, timeframe=timeframe, candles=candles)

    def fetch_realtime_ticker(self, symbol: str) -> TickerSnapshot:
        started_at = perf_counter()
        logger.info("Fetching ticker.")
        try:
            ticker = self.exchange.fetch_ticker(symbol)
        except Exception:
            logger.exception("Ticker request failed.")
            raise
        snapshot = TickerSnapshot(
            symbol=symbol,
            last=ticker.get("last"),
            bid=ticker.get("bid"),
            ask=ticker.get("ask"),
            base_volume=ticker.get("baseVolume"),
            quote_volume=ticker.get("quoteVolume"),
            timestamp=ticker.get("timestamp"),
        )
        logger.info("Ticker received in %.2fs.", perf_counter() - started_at)
        return snapshot

    def fetch_orderbook(self, symbol: str, limit: int = 50) -> OrderBookSnapshot:
        if limit <= 0:
            raise ValueError("limit must be positive")
        started_at = perf_counter()
        logger.info("Fetching order book (limit=%d).", limit)
        try:
            book = self.exchange.fetch_order_book(symbol, limit=limit)
        except Exception:
            logger.exception("Order-book request failed.")
            raise
        snapshot = OrderBookSnapshot(
            symbol=symbol,
            bids=book.get("bids", []),
            asks=book.get("asks", []),
            timestamp=book.get("timestamp"),
        )
        logger.info(
            "Order book received in %.2fs (bids=%d, asks=%d).",
            perf_counter() - started_at,
            len(snapshot.bids),
            len(snapshot.asks),
        )
        return snapshot

    def fetch_funding_history(self, symbol: str, limit: int = 200) -> list[OHLCVBar]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not hasattr(self.exchange, "fetch_funding_rate_history"):
            logger.debug("Funding history is not supported by this exchange client.")
            return []
        try:
            started_at = perf_counter()
            rows = self.exchange.fetch_funding_rate_history(symbol, limit=limit)
        except Exception:
            logger.warning(
                "Funding-history request failed; continuing without funding data.",
                exc_info=True,
            )
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
        logger.info(
            "Fetched %d funding records in %.2fs.",
            len(candles),
            perf_counter() - started_at,
        )
        return candles

    def fetch_recent_trades(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        started_at = perf_counter()
        logger.info("Fetching recent trades (limit=%d).", limit)
        try:
            trades = self.exchange.fetch_trades(symbol, limit=limit)
        except Exception:
            logger.exception("Recent-trades request failed.")
            raise
        result = [
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
        logger.info(
            "Fetched %d trades in %.2fs.",
            len(result),
            perf_counter() - started_at,
        )
        return result
