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
