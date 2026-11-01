from typing import Any

from framework.config.settings import Settings
from framework.tools.market_data import MarketDataTools


class FakeExchange:
    def __init__(self) -> None:
        self.rows: list[list[int | float]] = [
            [1_700_000_000_000, 10, 12, 9, 11, 5],
            [1_700_000_060_000, 11, 13, 10, 12, 7],
        ]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[list[int | float]]:
        return self.rows

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return {"last": 12, "bid": 11.9, "ask": 12.1, "timestamp": 1_700_000_060_000}

    def fetch_order_book(self, symbol: str, limit: int) -> dict[str, Any]:
        return {"bids": [[11.9, 2]], "asks": [[12.1, 3]], "timestamp": 1_700_000_060_000}

    def fetch_trades(self, symbol: str, limit: int) -> list[dict[str, Any]]:
        return [{"id": "1", "side": "buy", "price": 12, "amount": 1, "cost": 12}]


def test_market_data_normalizes_exchange_payloads() -> None:
    tools = MarketDataTools(Settings(), exchange=FakeExchange())
    candles = tools.fetch_ohlcv("BTC/USDT", "1m", 2)
    assert candles.candles[0].close == 11
    assert candles.candles[0].timestamp.tzinfo is not None
    assert tools.fetch_realtime_ticker("BTC/USDT").last == 12
    assert tools.fetch_orderbook("BTC/USDT").asks == [[12.1, 3]]
    assert tools.fetch_recent_trades("BTC/USDT")[0]["side"] == "buy"


def test_market_data_rejects_unsorted_candles() -> None:
    exchange = FakeExchange()
    exchange.rows.reverse()
    tools = MarketDataTools(Settings(), exchange=exchange)
    try:
        tools.fetch_ohlcv("BTC/USDT", "1m", 2)
    except ValueError as exc:
        assert "strictly time-ordered" in str(exc)
    else:
        raise AssertionError("Unsorted candles were accepted")


def test_market_data_uses_public_bybit_by_default(monkeypatch) -> None:
    import framework.tools.market_data as module

    calls: list[bool] = []

    class Exchange:
        def __init__(self, config) -> None:
            pass

        def set_sandbox_mode(self, enabled: bool) -> None:
            calls.append(enabled)

    monkeypatch.setattr(module.ccxt, "bybit", Exchange)
    MarketDataTools(Settings())
    MarketDataTools(Settings(market_data_testnet=True))
    assert calls == [False, True]


def test_market_data_serves_cached_candles_and_refreshes_only_the_tail() -> None:
    from framework.tools.candle_store import CandleStore

    minute = 60_000
    start = 1_790_000_000_000 // minute * minute

    class PagingExchange:
        def __init__(self) -> None:
            self.now = start + 300 * minute + 5_000
            self.calls: list[tuple[int, int]] = []

        def milliseconds(self) -> int:
            return self.now

        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
            until = (params or {})["until"]
            self.calls.append((since, until))
            live = self.now // minute * minute
            opens = range(since, min(until, live) + 1, minute)
            return [[open_ms, 1, 2, 0.5, 1.5, 3] for open_ms in list(opens)[:limit]]

    exchange = PagingExchange()
    tools = MarketDataTools(Settings(), exchange=exchange, candle_store=CandleStore(":memory:"))

    first = tools.fetch_ohlcv("BTC/USDT", "1m", limit=120)
    exchange.now += minute
    second = tools.fetch_ohlcv("BTC/USDT", "1m", limit=120)

    assert len(first.candles) == len(second.candles) == 120
    assert second.candles[-1].timestamp > first.candles[-1].timestamp
    assert len(exchange.calls) == 2
    assert exchange.calls[1][0] == start + 299 * minute


def test_injected_exchange_without_store_skips_the_cache() -> None:
    tools = MarketDataTools(Settings(), exchange=FakeExchange())
    assert tools.candle_cache is None


def test_exchange_keeps_ccxt_throttle_unless_configured(monkeypatch) -> None:
    import framework.tools.market_data as module

    configs: list[dict] = []

    class Exchange:
        def __init__(self, config) -> None:
            configs.append(config)

        def set_sandbox_mode(self, enabled: bool) -> None:
            pass

    monkeypatch.setattr(module.ccxt, "bybit", Exchange)
    MarketDataTools(Settings(candle_cache_enabled=False))
    MarketDataTools(Settings(candle_cache_enabled=False, exchange_rate_limit_ms=250))

    assert "rateLimit" not in configs[0]
    assert configs[1]["rateLimit"] == 250


def test_long_requests_page_even_with_the_cache_disabled(monkeypatch) -> None:
    import framework.tools.market_data as module

    minute = 60_000
    start = 1_790_000_000_000 // minute * minute
    calls: list[tuple[int, int]] = []

    class Exchange:
        def __init__(self, config) -> None:
            pass

        def set_sandbox_mode(self, enabled: bool) -> None:
            pass

        def milliseconds(self) -> int:
            return start + 3000 * minute + 1

        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
            until = (params or {})["until"]
            calls.append((since, until))
            live = self.milliseconds() // minute * minute
            opens = range(since, min(until, live) + 1, minute)
            return [[open_ms, 1, 2, 0.5, 1.5, 3] for open_ms in list(opens)[:limit]]

    monkeypatch.setattr(module.ccxt, "bybit", Exchange)
    tools = MarketDataTools(Settings(candle_cache_enabled=False))

    candles = tools.fetch_ohlcv("BTC/USDT", "1m", limit=2500).candles

    assert len(candles) == 2500
    assert len(calls) == 3  # Bybit returns at most 1000 candles per request
