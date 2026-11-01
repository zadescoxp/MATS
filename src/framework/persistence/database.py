import json
import sqlite3
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    config_snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    agent_name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error TEXT,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_steps_run_agent ON agent_steps(run_id, agent_name);
CREATE TABLE IF NOT EXISTS strategies (
    strategy_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    definition_json TEXT NOT NULL,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS backtests (
    backtest_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
    report_json TEXT NOT NULL,
    equity_curve_json TEXT NOT NULL,
    costs_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS critiques (
    critique_id TEXT PRIMARY KEY,
    backtest_id TEXT NOT NULL REFERENCES backtests(backtest_id),
    decision TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    revision_instructions TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    orders_json TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_positions (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    symbol TEXT NOT NULL,
    strategy_key TEXT NOT NULL,
    strategy_json TEXT NOT NULL,
    side TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    take_profit_price REAL,
    entry_order_id TEXT,
    protective_order_id TEXT,
    status TEXT NOT NULL,
    stop_reason TEXT,
    bars_held INTEGER NOT NULL DEFAULT 0,
    net_pnl REAL,
    events_json TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    last_bar_timestamp TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_positions_status_symbol
    ON live_positions(status, symbol);
CREATE INDEX IF NOT EXISTS idx_live_positions_strategy
    ON live_positions(strategy_key, status, updated_at);
CREATE TABLE IF NOT EXISTS strategy_stops (
    symbol TEXT NOT NULL,
    strategy_key TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    reason TEXT NOT NULL,
    stopped_at TEXT NOT NULL,
    PRIMARY KEY (symbol, strategy_key)
);
CREATE TABLE IF NOT EXISTS system_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES runs(run_id),
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    traceback TEXT,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_checkpoints (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    node_name TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('before', 'after', 'failed')),
    state_json TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (run_id, node_name, phase, timestamp)
);
CREATE TABLE IF NOT EXISTS entry_watchers (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    interval_seconds INTEGER NOT NULL,
    status TEXT NOT NULL,
    signal_json TEXT,
    last_checked_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_family_memory (
    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    family TEXT NOT NULL,
    template_name TEXT NOT NULL,
    regime TEXT NOT NULL,
    net_pnl REAL NOT NULL,
    decision TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_family_memory_lookup
    ON strategy_family_memory(symbol, regime, created_at);
CREATE TABLE IF NOT EXISTS strategy_trades (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    symbol TEXT NOT NULL,
    strategy_key TEXT NOT NULL,
    side TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    net_pnl REAL NOT NULL,
    reason TEXT NOT NULL,
    closed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_trades_key
    ON strategy_trades(strategy_key, closed_at);
"""


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _enable_wal(connection: sqlite3.Connection) -> None:
    deadline = time.monotonic() + 30
    delay = 0.05
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            code = exc.sqlite_errorcode
            is_locked = code is not None and code & 0xFF in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }
            if not is_locked or time.monotonic() >= deadline:
                raise
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
            delay = min(delay * 2, 0.5)


class Database:
    _initialization_lock = threading.RLock()

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        try:
            with self._initialization_lock:
                self.connection.execute("PRAGMA busy_timeout = 1000")
                _enable_wal(self.connection)
                self.connection.execute("PRAGMA busy_timeout = 30000")
                self.connection.executescript(SCHEMA)
                step_columns = {
                    row["name"] for row in self.connection.execute("PRAGMA table_info(agent_steps)")
                }
                if "tool_calls_json" not in step_columns:
                    self.connection.execute(
                        """ALTER TABLE agent_steps
                           ADD COLUMN tool_calls_json TEXT NOT NULL DEFAULT '[]'"""
                    )
                position_columns = {
                    row["name"]
                    for row in self.connection.execute("PRAGMA table_info(live_positions)")
                }
                if "last_bar_timestamp" not in position_columns:
                    self.connection.execute(
                        """ALTER TABLE live_positions
                           ADD COLUMN last_bar_timestamp TEXT NOT NULL DEFAULT ''"""
                    )
                watcher_columns = {
                    row["name"]
                    for row in self.connection.execute("PRAGMA table_info(entry_watchers)")
                }
                if watcher_columns and "last_bar_timestamp" not in watcher_columns:
                    self.connection.execute(
                        "ALTER TABLE entry_watchers ADD COLUMN last_bar_timestamp TEXT"
                    )
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def run_exists(self, run_id: str) -> bool:
        with self._lock:
            return (
                self.connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                is not None
            )

    def create_run(self, run_id: str, symbol: str, config: dict[str, Any]) -> None:
        now = _now()
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO runs (run_id, symbol, status, started_at, config_snapshot)
                   VALUES (?, ?, 'running', ?, ?)""",
                (run_id, symbol, now, json_dumps(config)),
            )

    def update_run_status(self, run_id: str, status: str) -> None:
        finished_at = _now() if status in {"completed", "failed", "stopped"} else None
        with self._lock, self.connection:
            self.connection.execute(
                "UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?",
                (status, finished_at, run_id),
            )

    def start_step(
        self, step_id: str, run_id: str, agent_name: str, input_data: dict[str, Any]
    ) -> bool:
        with self._lock, self.connection:
            cursor = self.connection.execute(
                """INSERT INTO agent_steps
                   (step_id, run_id, agent_name, input_json, status, started_at)
                   VALUES (?, ?, ?, ?, 'running', ?)
                   ON CONFLICT(step_id) DO UPDATE SET
                       input_json = excluded.input_json,
                       status = 'running',
                       error = NULL,
                       tool_calls_json = '[]',
                       started_at = excluded.started_at,
                       finished_at = NULL
                   WHERE agent_steps.status != 'completed'""",
                (step_id, run_id, agent_name, json_dumps(input_data), _now()),
            )
            return cursor.rowcount == 1

    def finish_step(
        self,
        step_id: str,
        *,
        output: dict[str, Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
        tokens_used: int = 0,
        cost: float = 0.0,
    ) -> None:
        status = "failed" if error else "completed"
        with self._lock, self.connection:
            self.connection.execute(
                """UPDATE agent_steps
                   SET output_json = ?, tool_calls_json = ?, status = ?, error = ?,
                       tokens_used = ?, cost = ?, finished_at = ?
                   WHERE step_id = ?""",
                (
                    json_dumps(output) if output is not None else None,
                    json_dumps(tool_calls or []),
                    status,
                    error,
                    tokens_used,
                    cost,
                    _now(),
                    step_id,
                ),
            )

    def step_completed(self, step_id: str) -> bool:
        with self._lock:
            row = self.connection.execute(
                "SELECT status FROM agent_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
        return bool(row and row["status"] == "completed")

    def step_output(self, step_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                """SELECT output_json FROM agent_steps
                   WHERE step_id = ? AND status = 'completed'""",
                (step_id,),
            ).fetchone()
        return json.loads(row["output_json"]) if row and row["output_json"] else None

    def checkpoint(self, run_id: str, node_name: str, phase: str, state: dict[str, Any]) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO node_checkpoints
                   (run_id, node_name, phase, state_json, timestamp) VALUES (?, ?, ?, ?, ?)""",
                (run_id, node_name, phase, json_dumps(state), _now()),
            )

    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                """SELECT state_json FROM node_checkpoints WHERE run_id = ?
                   ORDER BY timestamp DESC, rowid DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def start_entry_watcher(self, run_id: str, interval_seconds: int) -> None:
        now = _now()
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO entry_watchers
                   (run_id, interval_seconds, status, created_at, updated_at)
                   VALUES (?, ?, 'watching', ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                       interval_seconds = excluded.interval_seconds,
                       status = 'watching',
                       error = NULL,
                       updated_at = excluded.updated_at""",
                (run_id, interval_seconds, now, now),
            )

    def update_entry_watcher(
        self,
        run_id: str,
        *,
        status: str | None = None,
        signal: dict[str, Any] | None = None,
        error: str | None = None,
        last_bar: str | None = None,
    ) -> None:
        now = _now()
        with self._lock, self.connection:
            self.connection.execute(
                """UPDATE entry_watchers SET
                       status = COALESCE(?, status),
                       signal_json = COALESCE(?, signal_json),
                       last_bar_timestamp = COALESCE(?, last_bar_timestamp),
                       last_checked_at = ?,
                       error = ?,
                       updated_at = ?
                   WHERE run_id = ?""",
                (
                    status,
                    json_dumps(signal) if signal is not None else None,
                    last_bar,
                    now,
                    error,
                    now,
                    run_id,
                ),
            )

    def entry_watchers(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM entry_watchers"
        if active_only:
            query += " WHERE status IN ('watching', 'paused')"
        query += " ORDER BY created_at"
        with self._lock:
            rows = self.connection.execute(query).fetchall()
        return [
            {
                **dict(row),
                "signal": json.loads(row["signal_json"]) if row["signal_json"] else None,
            }
            for row in rows
        ]

    def record_family_result(
        self,
        *,
        symbol: str,
        family: str,
        template_name: str,
        regime: str,
        net_pnl: float,
        decision: str,
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO strategy_family_memory
                   (symbol, family, template_name, regime, net_pnl, decision, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (symbol, family, template_name, regime, net_pnl, decision, _now()),
            )

    def recent_family_results(
        self, symbol: str, regime: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        with self._lock:
            if regime:
                rows = self.connection.execute(
                    """SELECT family, template_name, regime, net_pnl, decision, created_at
                       FROM strategy_family_memory
                       WHERE symbol = ? AND regime = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (symbol, regime, limit),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """SELECT family, template_name, regime, net_pnl, decision, created_at
                       FROM strategy_family_memory
                       WHERE symbol = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (symbol, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def log_event(
        self,
        run_id: str | None,
        event_type: str,
        message: str,
        error: BaseException | None = None,
    ) -> None:
        trace = "".join(traceback.format_exception(error)) if error else None
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO system_events (run_id, type, message, traceback, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, event_type, message, trace, _now()),
            )

    def save_strategy(
        self, strategy_id: str, run_id: str, definition: dict[str, Any], version: int
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO strategies (strategy_id, run_id, definition_json, version)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(strategy_id) DO UPDATE SET
                       definition_json = excluded.definition_json, version = excluded.version""",
                (strategy_id, run_id, json_dumps(definition), version),
            )

    def save_backtest(
        self,
        backtest_id: str,
        strategy_id: str,
        report: dict[str, Any],
        equity_curve: list[dict[str, Any]],
        costs: dict[str, Any],
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO backtests
                   (backtest_id, strategy_id, report_json, equity_curve_json, costs_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(backtest_id) DO UPDATE SET
                       report_json = excluded.report_json,
                       equity_curve_json = excluded.equity_curve_json,
                       costs_json = excluded.costs_json""",
                (
                    backtest_id,
                    strategy_id,
                    json_dumps(report),
                    json_dumps(equity_curve),
                    json_dumps(costs),
                ),
            )

    def save_critique(
        self,
        critique_id: str,
        backtest_id: str,
        decision: str,
        issues: list[str],
        instructions: str,
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO critiques
                   (critique_id, backtest_id, decision, issues_json, revision_instructions)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(critique_id) DO UPDATE SET
                       decision = excluded.decision,
                       issues_json = excluded.issues_json,
                       revision_instructions = excluded.revision_instructions""",
                (critique_id, backtest_id, decision, json_dumps(issues), instructions),
            )

    def save_execution(
        self, execution_id: str, run_id: str, orders: list[dict[str, Any]], status: str
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO executions (execution_id, run_id, orders_json, status)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(execution_id) DO UPDATE SET
                       orders_json = excluded.orders_json, status = excluded.status""",
                (execution_id, run_id, json_dumps(orders), status),
            )

    def save_live_position(
        self,
        *,
        run_id: str,
        symbol: str,
        strategy_key: str,
        strategy: dict[str, Any],
        side: str,
        amount: float,
        entry_price: float,
        stop_price: float,
        take_profit_price: float | None,
        entry_order_id: str | None,
        protective_order_id: str | None,
        entry_bar_timestamp: str,
    ) -> None:
        now = _now()
        event = {
            "type": "opened",
            "timestamp": now,
            "side": side,
            "amount": amount,
            "entry_price": entry_price,
            "entry_order_id": entry_order_id,
        }
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO live_positions
                   (run_id, symbol, strategy_key, strategy_json, side, amount, entry_price,
                    stop_price, take_profit_price, entry_order_id, protective_order_id, status,
                    events_json, opened_at, last_bar_timestamp, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                       symbol = excluded.symbol, strategy_key = excluded.strategy_key,
                       strategy_json = excluded.strategy_json, side = excluded.side,
                       amount = excluded.amount, entry_price = excluded.entry_price,
                       stop_price = excluded.stop_price,
                       take_profit_price = excluded.take_profit_price,
                       entry_order_id = excluded.entry_order_id,
                       protective_order_id = excluded.protective_order_id,
                       status = 'open', stop_reason = NULL, bars_held = 0, net_pnl = NULL,
                       events_json = excluded.events_json, opened_at = excluded.opened_at,
                       last_bar_timestamp = excluded.last_bar_timestamp,
                       updated_at = excluded.updated_at""",
                (
                    run_id,
                    symbol,
                    strategy_key,
                    json_dumps(strategy),
                    side,
                    amount,
                    entry_price,
                    stop_price,
                    take_profit_price,
                    entry_order_id,
                    protective_order_id,
                    json_dumps([event]),
                    now,
                    entry_bar_timestamp,
                    now,
                ),
            )

    def open_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if symbol is None:
                rows = self.connection.execute(
                    "SELECT * FROM live_positions WHERE status = 'open' ORDER BY opened_at"
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """SELECT * FROM live_positions WHERE status = 'open' AND symbol = ?
                       ORDER BY opened_at""",
                    (symbol,),
                ).fetchall()
        return [
            {
                **dict(row),
                "strategy": json.loads(row["strategy_json"]),
                "events": json.loads(row["events_json"]),
            }
            for row in rows
        ]

    def update_live_position(
        self,
        run_id: str,
        *,
        status: str,
        event_type: str,
        reason: str,
        bars_held: int,
        net_pnl: float | None = None,
        amount: float | None = None,
        protective_order_id: str | None = None,
        last_bar_timestamp: str | None = None,
    ) -> None:
        now = _now()
        with self._lock, self.connection:
            row = self.connection.execute(
                "SELECT events_json FROM live_positions WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"No persisted live position for run_id={run_id!r}")
            events = json.loads(row["events_json"])
            events.append(
                {
                    "type": event_type,
                    "timestamp": now,
                    "reason": reason,
                    "net_pnl": net_pnl,
                }
            )
            self.connection.execute(
                """UPDATE live_positions
                   SET status = ?, stop_reason = ?, bars_held = ?, net_pnl = ?,
                       amount = COALESCE(?, amount),
                       protective_order_id = COALESCE(?, protective_order_id),
                       last_bar_timestamp = COALESCE(?, last_bar_timestamp),
                       events_json = ?, updated_at = ?
                   WHERE run_id = ?""",
                (
                    status,
                    reason,
                    bars_held,
                    net_pnl,
                    amount,
                    protective_order_id,
                    last_bar_timestamp,
                    json_dumps(events),
                    now,
                    run_id,
                ),
            )

    def update_position_progress(
        self, run_id: str, *, bars_held: int, last_bar_timestamp: str
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """UPDATE live_positions SET bars_held = ?, last_bar_timestamp = ?, updated_at = ?
                   WHERE run_id = ? AND status = 'open'""",
                (bars_held, last_bar_timestamp, _now(), run_id),
            )

    def record_closed_trade(
        self,
        *,
        run_id: str,
        symbol: str,
        strategy_key: str,
        side: str,
        amount: float,
        entry_price: float,
        exit_price: float | None,
        net_pnl: float,
        reason: str,
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO strategy_trades
                   (run_id, symbol, strategy_key, side, amount, entry_price, exit_price,
                    net_pnl, reason, closed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    symbol,
                    strategy_key,
                    side,
                    amount,
                    entry_price,
                    exit_price,
                    net_pnl,
                    reason,
                    _now(),
                ),
            )

    def strategy_trades(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM strategy_trades WHERE run_id = ? ORDER BY trade_id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_strategy_expectancy(
        self, strategy_key: str, lookback: int, *, min_trades: int = 1
    ) -> float | None:
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        with self._lock:
            rows = self.connection.execute(
                """SELECT net_pnl FROM strategy_trades
                   WHERE strategy_key = ?
                   ORDER BY trade_id DESC LIMIT ?""",
                (strategy_key, lookback),
            ).fetchall()
        if not rows or len(rows) < min_trades:
            return None
        return sum(float(row["net_pnl"]) for row in rows) / len(rows)

    def mark_strategy_stopped(
        self, symbol: str, strategy_key: str, run_id: str, reason: str
    ) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO strategy_stops (symbol, strategy_key, run_id, reason, stopped_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(symbol, strategy_key) DO UPDATE SET
                       run_id = excluded.run_id, reason = excluded.reason,
                       stopped_at = excluded.stopped_at""",
                (symbol, strategy_key, run_id, reason, _now()),
            )

    def strategy_stop_reason(self, symbol: str, strategy_key: str) -> str | None:
        with self._lock:
            row = self.connection.execute(
                """SELECT reason FROM strategy_stops
                   WHERE symbol = ? AND strategy_key = ?""",
                (symbol, strategy_key),
            ).fetchone()
        return str(row["reason"]) if row else None

    def clear_strategy_stop(self, symbol: str, strategy_key: str) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "DELETE FROM strategy_stops WHERE symbol = ? AND strategy_key = ?",
                (symbol, strategy_key),
            )

    def clear_strategy_stops(self, symbol: str) -> int:
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM strategy_stops WHERE symbol = ?", (symbol,)
            )
            return cursor.rowcount

    def resumable_run(self, run_id: str | None = None) -> sqlite3.Row | None:
        with self._lock:
            if run_id:
                return self.connection.execute(
                    """SELECT * FROM runs WHERE run_id = ?
                       AND status IN ('running', 'failed', 'stopped')""",
                    (run_id,),
                ).fetchone()
            return self.connection.execute(
                """SELECT * FROM runs WHERE status IN ('running', 'failed', 'stopped')
                   ORDER BY started_at DESC LIMIT 1"""
            ).fetchone()

    def completed_step_output(self, run_id: str, agent_name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                """SELECT output_json FROM agent_steps
                   WHERE run_id = ? AND agent_name = ? AND status = 'completed'
                   ORDER BY started_at DESC LIMIT 1""",
                (run_id, agent_name),
            ).fetchone()
        return json.loads(row["output_json"]) if row and row["output_json"] else None


def _now() -> str:
    return datetime.now(UTC).isoformat()
