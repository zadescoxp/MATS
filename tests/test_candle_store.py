import threading
from datetime import UTC, datetime

from framework.tools.candle_store import CandleCache, CandleStore, candle_open, timeframe_ms

MINUTE = 60_000
START = 1_790_000_000_000 // MINUTE * MINUTE


class FakeExchange:
    """Serves 1m candles like Bybit: oldest first from `since`, bounded by `until`."""

    def __init__(self, now: int, *, missing: set[int] | None = None, lag_bars: int = 0):
        self.now = now
        self.missing = missing or set()
        self.lag_bars = lag_bars
        self.revisions: dict[int, float] = {}
        self.calls: list[tuple[int, int]] = []

    def fetch_page(self, symbol, timeframe, since, until, limit):
        self.calls.append((since, until))
        step = timeframe_ms(timeframe)
        live_open = candle_open(self.now, timeframe)
        # lag_bars closed candles (and the forming one) are not published yet.
        newest = live_open if not self.lag_bars else live_open - (self.lag_bars + 1) * step
        rows = []
        open_ms = candle_open(since, timeframe)
        open_ms = open_ms if open_ms >= since else open_ms + step
        while open_ms <= min(until, newest) and len(rows) < limit:
            if open_ms not in self.missing:
                price = self.revisions.get(open_ms, 100 + (open_ms - START) / MINUTE)
                volume = 0.5 if open_ms == live_open else 10.0
                rows.append([open_ms, price, price + 1, price - 1, price, volume])
            open_ms += step
        return rows


def _cache(store, exchange, refresh_bars=2):
    return CandleCache(
        store, "test", exchange.fetch_page, lambda: exchange.now, refresh_bars=refresh_bars
    )


def _opens(rows):
    return [int(row[0]) for row in rows]


def test_second_run_downloads_only_new_candles_and_returns_a_full_window():
    store = CandleStore(":memory:")
    exchange = FakeExchange(now=START + 600 * MINUTE + 30_000)
    cache = _cache(store, exchange)

    first, report = cache.latest("BTC/USDT", "1m", 500)
    assert report.requests == 1
    assert len(first) == 500
    assert first[-1][0] == START + 600 * MINUTE  # the forming candle comes last
    assert first[-1][5] == 0.5

    exchange.now += 5 * MINUTE
    exchange.calls.clear()
    second, report = cache.latest("BTC/USDT", "1m", 500)

    assert len(exchange.calls) == 1
    since, _until = exchange.calls[0]
    assert since == START + 600 * MINUTE  # only the five candles closed since the last run
    assert report.stored == 5
    assert _opens(second) == [START + (106 + index) * MINUTE for index in range(500)]


def test_forming_candle_is_never_stored():
    store = CandleStore(":memory:")
    exchange = FakeExchange(now=START + 100 * MINUTE + 1)
    _cache(store, exchange).latest("BTC/USDT", "1m", 50)

    stored = store.read("test", "BTC/USDT", "1m", 0, START + 10_000 * MINUTE)
    assert _opens(stored)[-1] == START + 99 * MINUTE


def test_large_windows_page_without_skipping_candles():
    store = CandleStore(":memory:")
    exchange = FakeExchange(now=START + 3000 * MINUTE)

    rows, report = _cache(store, exchange).latest("BTC/USDT", "1m", 2500)

    assert report.requests == 3
    assert _opens(rows) == [START + (501 + index) * MINUTE for index in range(2500)]


def test_exchange_gaps_are_verified_once_and_not_refetched():
    store = CandleStore(":memory:")
    hole = {START + 250 * MINUTE, START + 251 * MINUTE}
    exchange = FakeExchange(now=START + 400 * MINUTE, missing=hole)
    cache = _cache(store, exchange)

    rows, report = cache.latest("BTC/USDT", "1m", 300)
    assert report.gaps == 2
    assert not hole.intersection(_opens(rows))
    assert len(rows) == 298

    exchange.now += MINUTE
    exchange.calls.clear()
    _rows, report = cache.latest("BTC/USDT", "1m", 300)
    assert report.gaps == 0
    assert all(since > START + 251 * MINUTE for since, _until in exchange.calls)


def test_lagging_exchange_candle_is_fetched_on_the_next_run():
    store = CandleStore(":memory:")
    exchange = FakeExchange(now=START + 200 * MINUTE, lag_bars=1)
    cache = _cache(store, exchange, refresh_bars=1)

    rows, _report = cache.latest("BTC/USDT", "1m", 100)
    assert _opens(rows)[-1] == START + 198 * MINUTE  # 199 not published yet, no live bar

    exchange.lag_bars = 0
    rows, _report = cache.latest("BTC/USDT", "1m", 100)
    assert START + 199 * MINUTE in _opens(rows)
    assert _opens(rows) == [START + (101 + index) * MINUTE for index in range(100)]


def test_recently_closed_candles_pick_up_exchange_revisions():
    store = CandleStore(":memory:")
    exchange = FakeExchange(now=START + 50 * MINUTE)
    cache = _cache(store, exchange)
    cache.latest("BTC/USDT", "1m", 20)

    exchange.revisions[START + 49 * MINUTE] = 999.0
    rows, _report = cache.latest("BTC/USDT", "1m", 20)

    assert rows[-2][4] == 999.0


def test_widening_the_window_fetches_only_the_older_part():
    store = CandleStore(":memory:")
    exchange = FakeExchange(now=START + 2000 * MINUTE)
    cache = _cache(store, exchange)
    cache.latest("BTC/USDT", "1m", 200)

    exchange.calls.clear()
    rows, _report = cache.latest("BTC/USDT", "1m", 600)

    assert len(rows) == 600
    assert exchange.calls[0] == (START + 1401 * MINUTE, START + 1800 * MINUTE)
    assert store.coverage("test", "BTC/USDT", "1m") == [
        (START + 1401 * MINUTE, START + 1999 * MINUTE)
    ]


def test_sources_and_symbols_are_cached_separately():
    store = CandleStore(":memory:")
    exchange = FakeExchange(now=START + 100 * MINUTE)
    _cache(store, exchange).latest("BTC/USDT", "1m", 10)

    assert store.coverage("test", "ETH/USDT", "1m") == []
    assert store.coverage("other", "BTC/USDT", "1m") == []


def test_weekly_candles_open_on_monday():
    wednesday = int(datetime(2026, 10, 28, 15, tzinfo=UTC).timestamp() * 1000)
    monday = datetime.fromtimestamp(candle_open(wednesday, "1w") / 1000, tz=UTC)
    assert (monday.weekday(), monday.hour) == (0, 0)
    assert candle_open(wednesday, "1d") == int(
        datetime(2026, 10, 28, tzinfo=UTC).timestamp() * 1000
    )
    assert timeframe_ms("1M") is None


def test_concurrent_runs_share_one_consistent_series(tmp_path):
    store = CandleStore(tmp_path / "candles.db")
    exchange = FakeExchange(now=START + 1500 * MINUTE)
    results = []

    def run():
        results.append(_cache(store, exchange).latest("BTC/USDT", "1m", 1000)[0])

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(_opens(rows) == _opens(results[0]) for rows in results)
    assert len(results[0]) == 1000
    assert store.coverage("test", "BTC/USDT", "1m") == [
        (START + 501 * MINUTE, START + 1499 * MINUTE)
    ]
    store.close()
