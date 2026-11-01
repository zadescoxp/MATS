"""Local OHLCV cache that downloads only the candles a run does not have yet.

Only closed candles are stored. Alongside them the store keeps the time ranges it has
verified with the exchange, so "no row" can be told apart from "never fetched": a run
refetches every unverified range, and a candle missing inside a verified range is a gap
in the exchange's own history rather than a hole in the cache.
"""

import logging
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PAGE_SIZE = 1000  # Bybit returns at most 1000 klines per request.
_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
# Weekly candles open on Monday 00:00 UTC; the Unix epoch fell on a Thursday.
_WEEK_OFFSET_MS = 4 * 86_400_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_ms INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (source, symbol, timeframe, open_ms)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS candle_coverage (
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    PRIMARY KEY (source, symbol, timeframe, start_ms)
);
"""

Row = Sequence[float]
FetchPage = Callable[[str, str, int, int, int], list[Row]]


def timeframe_ms(timeframe: str) -> int | None:
    """Fixed candle length in ms, or None for calendar timeframes such as months."""
    unit = timeframe[-1:]
    if unit not in _UNIT_MS or not timeframe[:-1].isdigit():
        return None
    return int(timeframe[:-1]) * _UNIT_MS[unit]


def candle_open(timestamp_ms: int, timeframe: str) -> int:
    """Open time of the candle that contains timestamp_ms."""
    length = timeframe_ms(timeframe)
    if length is None:
        raise ValueError(f"Timeframe {timeframe!r} has no fixed length")
    offset = _WEEK_OFFSET_MS if timeframe.endswith("w") else 0
    return (timestamp_ms - offset) // length * length + offset


@dataclass(frozen=True)
class SyncReport:
    requests: int
    stored: int
    gaps: int


class CandleStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._series_locks: dict[tuple[str, str, str], threading.Lock] = {}
        self.connection = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30, isolation_level=None
        )
        self.connection.execute("PRAGMA busy_timeout = 30000")
        if str(self.path) != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(_SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def series_lock(self, source: str, symbol: str, timeframe: str) -> threading.Lock:
        with self._lock:
            return self._series_locks.setdefault((source, symbol, timeframe), threading.Lock())

    def coverage(self, source: str, symbol: str, timeframe: str) -> list[tuple[int, int]]:
        with self._lock:
            rows = self.connection.execute(
                """SELECT start_ms, end_ms FROM candle_coverage
                   WHERE source = ? AND symbol = ? AND timeframe = ? ORDER BY start_ms""",
                (source, symbol, timeframe),
            ).fetchall()
        return [(int(start), int(end)) for start, end in rows]

    def save(
        self,
        source: str,
        symbol: str,
        timeframe: str,
        rows: list[Row],
        verified: tuple[int, int] | None,
    ) -> None:
        """Upsert closed candles and merge the verified range in one transaction."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.executemany(
                    """INSERT INTO candles
                       (source, symbol, timeframe, open_ms, open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source, symbol, timeframe, open_ms) DO UPDATE SET
                           open = excluded.open, high = excluded.high, low = excluded.low,
                           close = excluded.close, volume = excluded.volume""",
                    [
                        (source, symbol, timeframe, int(row[0]), *map(float, row[1:6]))
                        for row in rows
                    ],
                )
                if verified is not None:
                    self._merge_coverage(source, symbol, timeframe, verified)
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def _merge_coverage(
        self, source: str, symbol: str, timeframe: str, verified: tuple[int, int]
    ) -> None:
        step = timeframe_ms(timeframe) or 1
        start, end = verified
        # Adjacent ranges (end + one candle == start) merge into one.
        touching = self.connection.execute(
            """SELECT start_ms, end_ms FROM candle_coverage
               WHERE source = ? AND symbol = ? AND timeframe = ?
                 AND start_ms <= ? AND end_ms >= ?""",
            (source, symbol, timeframe, end + step, start - step),
        ).fetchall()
        for other_start, other_end in touching:
            start, end = min(start, other_start), max(end, other_end)
        self.connection.execute(
            """DELETE FROM candle_coverage
               WHERE source = ? AND symbol = ? AND timeframe = ?
                 AND start_ms <= ? AND end_ms >= ?""",
            (source, symbol, timeframe, end + step, start - step),
        )
        self.connection.execute(
            """INSERT INTO candle_coverage (source, symbol, timeframe, start_ms, end_ms)
               VALUES (?, ?, ?, ?, ?)""",
            (source, symbol, timeframe, start, end),
        )

    def read(
        self, source: str, symbol: str, timeframe: str, start_ms: int, end_ms: int
    ) -> list[list[float]]:
        with self._lock:
            rows = self.connection.execute(
                """SELECT open_ms, open, high, low, close, volume FROM candles
                   WHERE source = ? AND symbol = ? AND timeframe = ?
                     AND open_ms BETWEEN ? AND ? ORDER BY open_ms""",
                (source, symbol, timeframe, start_ms, end_ms),
            ).fetchall()
        return [list(row) for row in rows]


class CandleCache:
    """Serve the latest N candles, downloading only unverified or refreshable ranges."""

    def __init__(
        self,
        store: CandleStore,
        source: str,
        fetch_page: FetchPage,
        now_ms: Callable[[], int],
        *,
        refresh_bars: int = 2,
    ):
        self.store = store
        self.source = source
        self.fetch_page = fetch_page
        self.now_ms = now_ms
        self.refresh_bars = refresh_bars

    def latest(self, symbol: str, timeframe: str, limit: int) -> tuple[list[Row], SyncReport]:
        """The last `limit` candles, oldest first, ending with the still-forming candle."""
        step = timeframe_ms(timeframe)
        if step is None:
            raise ValueError(f"Timeframe {timeframe!r} cannot be cached")
        with self.store.series_lock(self.source, symbol, timeframe):
            live_open = candle_open(self.now_ms(), timeframe)
            last_closed = live_open - step
            window_start = live_open - (limit - 1) * step
            missing = self._missing(symbol, timeframe, window_start, last_closed, step)
            live, report = self._download(symbol, timeframe, missing, live_open, step)
            rows = self.store.read(self.source, symbol, timeframe, window_start, last_closed)
        if live is not None:
            rows.append(list(live))
        return rows[-limit:], report

    def _missing(
        self, symbol: str, timeframe: str, start: int, end: int, step: int
    ) -> list[tuple[int, int]]:
        gaps: list[tuple[int, int]] = []
        cursor = start
        for covered_start, covered_end in self.store.coverage(self.source, symbol, timeframe):
            if covered_end < cursor:
                continue
            if covered_start > end:
                break
            if covered_start > cursor:
                gaps.append((cursor, covered_start - step))
            cursor = max(cursor, covered_end + step)
        if cursor <= end:
            gaps.append((cursor, end))
        # The newest closed candles are always re-read: exchanges can revise them briefly
        # after the close, and the forming candle comes back with the same request.
        refresh_start = max(start, end - (self.refresh_bars - 1) * step)
        gaps.append((refresh_start, end))
        return _merge_ranges(gaps, step)

    def _download(
        self,
        symbol: str,
        timeframe: str,
        ranges: list[tuple[int, int]],
        live_open: int,
        step: int,
    ) -> tuple[Row | None, SyncReport]:
        requests = stored = gaps = 0
        live: Row | None = None
        for index, (range_start, range_end) in enumerate(ranges):
            is_last = index == len(ranges) - 1
            cursor = range_start
            unverified_from: int | None = None
            while cursor <= range_end:
                page_end = min(range_end, cursor + (PAGE_SIZE - 1) * step)
                # The final page also asks for the forming candle so it stays fresh.
                until = live_open if is_last and page_end == range_end else page_end
                rows = self.fetch_page(symbol, timeframe, cursor, until, PAGE_SIZE)
                requests += 1
                live = next((row for row in rows if int(row[0]) == live_open), live)
                closed = sorted(
                    (row for row in rows if cursor <= int(row[0]) <= page_end),
                    key=lambda row: int(row[0]),
                )
                if not closed:
                    # Nothing yet: either before the listing, an outage, or exchange lag.
                    # Leave it unverified; it is confirmed once later candles appear.
                    unverified_from = cursor if unverified_from is None else unverified_from
                    cursor = page_end + step
                    continue
                verified_start = cursor if unverified_from is None else unverified_from
                verified_end = int(closed[-1][0])
                gaps += _count_gaps(closed, step)
                self.store.save(
                    self.source, symbol, timeframe, closed, (verified_start, verified_end)
                )
                stored += len(closed)
                unverified_from = None
                # Keep paging from the newest returned candle: a short page can mean a gap
                # in the exchange's history, with more candles after it in this range.
                cursor = verified_end + step
        if gaps:
            logger.warning(
                "Exchange history has %d missing candle(s) (symbol=%s, timeframe=%s).",
                gaps,
                symbol,
                timeframe,
            )
        return live, SyncReport(requests=requests, stored=stored, gaps=gaps)


def _merge_ranges(ranges: list[tuple[int, int]], step: int) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start > end:
            continue
        if merged and start <= merged[-1][1] + step:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _count_gaps(rows: list[Row], step: int) -> int:
    expected = (int(rows[-1][0]) - int(rows[0][0])) // step + 1
    return max(expected - len(rows), 0)
