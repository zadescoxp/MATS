"""Store each candle list once and reference it from checkpoints and step records.

A run's candles are identical in every checkpoint and step record it writes, so saving
them inline multiplied the same data ~20 times per run. Lists that look like OHLCV bars
are replaced with a small reference to a compressed copy keyed by a hash of their exact
JSON, and restored byte-for-byte when read. Rows written before this change still hold
plain lists and read back unchanged.
"""

import hashlib
import json
import sqlite3
import threading
import zlib
from collections import OrderedDict
from typing import Any

REF_KEY = "$candles"
MIN_SERIES_BARS = 20
_CANDLE_KEYS = frozenset({"timestamp", "open", "high", "low", "close", "volume"})
_CACHE_SIZE = 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS candle_series (
    series_id TEXT PRIMARY KEY,
    bar_count INTEGER NOT NULL,
    first_timestamp TEXT,
    last_timestamp TEXT,
    data BLOB NOT NULL
);
"""


def is_candle_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= MIN_SERIES_BARS
        and all(isinstance(item, dict) and _CANDLE_KEYS <= item.keys() for item in value)
    )


def is_series_ref(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get(REF_KEY), str)


class CandleSeriesStore:
    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock):
        self.connection = connection
        self.lock = lock
        self._cache: OrderedDict[str, list[Any]] = OrderedDict()

    def pack(self, value: Any) -> Any:
        """Replace every candle list in a JSON-compatible value with a reference."""
        if is_candle_list(value):
            return self._store(value)
        if isinstance(value, dict):
            return {key: self.pack(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.pack(item) for item in value]
        return value

    def unpack(self, value: Any) -> Any:
        """Restore every reference to the exact candle list it was made from."""
        if is_series_ref(value):
            return self._load(value[REF_KEY])
        if isinstance(value, dict):
            return {key: self.unpack(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.unpack(item) for item in value]
        return value

    def _store(self, bars: list[dict[str, Any]]) -> dict[str, Any]:
        encoded = json.dumps(bars, separators=(",", ":"), sort_keys=True).encode()
        series_id = hashlib.sha256(encoded).hexdigest()[:32]
        with self.lock:
            self.connection.execute(
                """INSERT OR IGNORE INTO candle_series
                   (series_id, bar_count, first_timestamp, last_timestamp, data)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    series_id,
                    len(bars),
                    _timestamp(bars[0]),
                    _timestamp(bars[-1]),
                    zlib.compress(encoded, 6),
                ),
            )
        self._remember(series_id, bars)
        return {
            REF_KEY: series_id,
            "count": len(bars),
            "first": _timestamp(bars[0]),
            "last": _timestamp(bars[-1]),
        }

    def _load(self, series_id: str) -> list[Any]:
        cached = self._cache.get(series_id)
        if cached is not None:
            self._cache.move_to_end(series_id)
            return json.loads(json.dumps(cached))  # callers may mutate their copy
        with self.lock:
            row = self.connection.execute(
                "SELECT data FROM candle_series WHERE series_id = ?", (series_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Candle series {series_id} is missing from the database")
        bars = json.loads(zlib.decompress(row[0]))
        self._remember(series_id, bars)
        return json.loads(json.dumps(bars))

    def _remember(self, series_id: str, bars: list[Any]) -> None:
        self._cache[series_id] = bars
        self._cache.move_to_end(series_id)
        while len(self._cache) > _CACHE_SIZE:
            self._cache.popitem(last=False)


def _timestamp(bar: dict[str, Any]) -> str | None:
    value = bar.get("timestamp")
    return None if value is None else str(value)
