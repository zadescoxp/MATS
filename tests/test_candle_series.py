import json
import sqlite3
import zlib

import pytest

from framework.persistence.candle_series import REF_KEY, is_series_ref
from framework.persistence.database import Database


def _bars(count: int, start_price: float = 100.0) -> list[dict]:
    return [
        {
            "timestamp": f"2026-11-01T{index // 60:02d}:{index % 60:02d}:00Z",
            "open": start_price + index,
            "high": start_price + index + 1,
            "low": start_price + index - 1,
            "close": start_price + index + 0.5,
            "volume": 10.0 + index,
        }
        for index in range(count)
    ]


def _state(bars_1h, bars_4h) -> dict:
    return {
        "run_id": "run-1",
        "raw_data": {"1h": bars_1h, "4h": bars_4h},
        "market_context": {"pair_candles": {"1h": bars_1h}, "ticker": {"last": 1.0}},
        "errors": [],
    }


def test_checkpoint_round_trip_is_exact_and_stores_each_series_once() -> None:
    database = Database(":memory:")
    try:
        database.create_run("run-1", "BTC/USDT", {})
        state = _state(_bars(500), _bars(300, 200.0))
        for node in ("data_worker", "analyst", "regime"):
            database.checkpoint("run-1", node, "before", state)
            database.checkpoint("run-1", node, "after", state)

        restored = database.latest_checkpoint("run-1")

        assert restored == json.loads(json.dumps(state))
        assert (
            database.connection.execute("SELECT COUNT(*) FROM candle_series").fetchone()[0] == 2
        )  # 1h (shared with pair_candles) and 4h
        stored = database.connection.execute(
            "SELECT state_json FROM node_checkpoints LIMIT 1"
        ).fetchone()["state_json"]
        assert len(stored) < 1_000
        assert REF_KEY in stored
    finally:
        database.close()


def test_step_output_with_model_objects_restores_the_json_form() -> None:
    from datetime import UTC, datetime, timedelta

    from framework.schemas.models import OHLCVBar

    database = Database(":memory:")
    try:
        database.create_run("run-1", "BTC/USDT", {})
        start = datetime(2026, 11, 1, tzinfo=UTC)
        bars = [
            OHLCVBar(
                timestamp=start + timedelta(hours=index),
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=5,
            )
            for index in range(50)
        ]
        database.start_step("step-1", "run-1", "data_worker", {"symbol": "BTC/USDT"})
        database.finish_step("step-1", output={"raw_data": {"1h": bars}})

        restored = database.step_output("step-1")

        assert restored == {"raw_data": {"1h": [bar.model_dump(mode="json") for bar in bars]}}
        raw = database.connection.execute(
            "SELECT output_json FROM agent_steps WHERE step_id = 'step-1'"
        ).fetchone()["output_json"]
        assert REF_KEY in raw
    finally:
        database.close()


def test_short_lists_and_non_candle_lists_stay_inline() -> None:
    database = Database(":memory:")
    try:
        value = {"few": _bars(5), "numbers": list(range(100)), "items": [{"a": 1}] * 30}
        assert database.series.pack(value) == value
    finally:
        database.close()


def test_legacy_rows_with_inline_candles_still_read() -> None:
    database = Database(":memory:")
    try:
        database.create_run("run-1", "BTC/USDT", {})
        state = _state(_bars(120), _bars(60))
        database.connection.execute(
            """INSERT INTO node_checkpoints (run_id, node_name, phase, state_json, timestamp)
               VALUES ('run-1', 'data_worker', 'after', ?, '2026-11-01T00:00:00Z')""",
            (json.dumps(state),),
        )
        assert database.latest_checkpoint("run-1") == state
    finally:
        database.close()


def test_missing_series_is_reported_clearly() -> None:
    database = Database(":memory:")
    try:
        with pytest.raises(KeyError, match="missing"):
            database.series.unpack({REF_KEY: "0" * 32, "count": 3})
    finally:
        database.close()


def test_series_data_is_compressed_exact_json() -> None:
    database = Database(":memory:")
    try:
        bars = _bars(200)
        ref = database.series.pack(bars)
        assert is_series_ref(ref) and ref["count"] == 200
        blob = database.connection.execute(
            "SELECT data FROM candle_series WHERE series_id = ?", (ref[REF_KEY],)
        ).fetchone()[0]
        assert json.loads(zlib.decompress(blob)) == bars
        assert len(blob) < len(json.dumps(bars)) / 3
    finally:
        database.close()


def test_compact_storage_rewrites_old_rows_and_drops_langgraph_tables(tmp_path) -> None:
    path = tmp_path / "state.db"
    database = Database(path)
    database.create_run("run-1", "BTC/USDT", {})
    state = _state(_bars(500), _bars(500, 300.0))
    for index in range(10):
        database.connection.execute(
            """INSERT INTO node_checkpoints (run_id, node_name, phase, state_json, timestamp)
               VALUES ('run-1', 'node', 'after', ?, ?)""",
            (json.dumps(state), f"2026-11-01T00:00:{index:02d}Z"),
        )
    database.connection.execute(
        """INSERT INTO agent_steps (step_id, run_id, agent_name, input_json, output_json,
               status, started_at)
           VALUES ('s1', 'run-1', 'data_worker', ?, ?, 'completed', '2026-11-01T00:00:00Z')""",
        (json.dumps(state), json.dumps({"raw_data": state["raw_data"]})),
    )
    database.connection.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint BLOB)")
    database.connection.execute("CREATE TABLE writes (thread_id TEXT, value BLOB)")
    database.close()

    database = Database(path)
    try:
        result = database.compact_storage(batch_size=3)

        assert result["rows_rewritten"] == 11
        assert result["bytes_after"] < result["bytes_before"]
        assert database.latest_checkpoint("run-1") == state
        assert database.step_output("s1") == {"raw_data": state["raw_data"]}
        tables = {
            row[0]
            for row in database.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert not {"checkpoints", "writes"} & tables
        assert database.compact_storage()["rows_rewritten"] == 0
    finally:
        database.close()
    assert sqlite3.connect(path).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
