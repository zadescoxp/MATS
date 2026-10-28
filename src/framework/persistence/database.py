import json
import sqlite3
import threading
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


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def create_run(self, run_id: str, symbol: str, config: dict[str, Any]) -> None:
        now = _now()
        with self._lock, self.connection:
            self.connection.execute(
                """INSERT INTO runs (run_id, symbol, status, started_at, config_snapshot)
                   VALUES (?, ?, 'running', ?, ?)""",
                (run_id, symbol, now, json_dumps(config)),
            )

    def update_run_status(self, run_id: str, status: str) -> None:
        finished_at = _now() if status in {"completed", "failed"} else None
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
        error: str | None = None,
        tokens_used: int = 0,
        cost: float = 0.0,
    ) -> None:
        status = "failed" if error else "completed"
        with self._lock, self.connection:
            self.connection.execute(
                """UPDATE agent_steps
                   SET output_json = ?, status = ?, error = ?, tokens_used = ?, cost = ?,
                       finished_at = ?
                   WHERE step_id = ?""",
                (
                    json_dumps(output) if output is not None else None,
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

    def resumable_run(self, run_id: str | None = None) -> sqlite3.Row | None:
        with self._lock:
            if run_id:
                return self.connection.execute(
                    "SELECT * FROM runs WHERE run_id = ? AND status IN ('running', 'failed')",
                    (run_id,),
                ).fetchone()
            return self.connection.execute(
                """SELECT * FROM runs WHERE status IN ('running', 'failed')
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
