import hashlib
import json
import logging
import math
import re
import sqlite3
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from framework.agents.execution import ExecutionNode
from framework.agents.risk_assessment import RiskAssessmentNode
from framework.config.settings import Settings, get_settings
from framework.core.graph import TradingFramework
from framework.core.state import GraphState
from framework.persistence.database import Database
from framework.tools.execution import ExecutionTools

_ALLOWED_ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000"}
_ALLOWED_TIMEFRAMES = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "12h",
    "1d",
    "1w",
}
_SYMBOL = re.compile(r"^[A-Z0-9]{2,20}/[A-Z0-9]{2,20}$")
_ENTRY_WATCH_INTERVAL_SECONDS = 60
logger = logging.getLogger(__name__)


class DashboardService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if self.settings.database_path.exists():
            database = Database(self.settings.database_path)
            database.close()
        self._lock = threading.RLock()
        self._stop_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._operations: dict[str, dict[str, str]] = {}
        self._restore_entry_watchers()

    def state(self) -> dict[str, Any]:
        data = _read_snapshot(self.settings.database_path)
        with self._lock:
            data["settings"] = {
                "dry_run": self.settings.dry_run,
                "kill_switch": self.settings.kill_switch,
                "symbols": self.settings.symbols,
                "timeframes": self.settings.timeframes,
            }
            data["operations"] = list(self._operations.values())
            data["active_run_ids"] = [
                run_id for run_id, thread in self._threads.items() if thread.is_alive()
            ]
        return data

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbols = _symbols(payload.get("symbols"), self.settings.symbols)
        timeframes = _timeframes(payload.get("timeframes"), self.settings.timeframes)
        account_equity = payload.get("account_equity")
        if account_equity is not None and (
            isinstance(account_equity, bool)
            or not isinstance(account_equity, int | float)
            or not math.isfinite(account_equity)
            or account_equity <= 0
        ):
            raise ValueError("account_equity must be a positive finite number.")
        dry_run_override = payload.get("dry_run")
        if dry_run_override is not None and not isinstance(dry_run_override, bool):
            raise ValueError("dry_run must be a boolean.")

        with self._lock:
            effective_dry_run = (
                self.settings.dry_run if dry_run_override is None else dry_run_override
            )
            if not effective_dry_run and not self.settings.kill_switch:
                active = [
                    run_id
                    for run_id, event in self._stop_events.items()
                    if not event.is_set() and self._run_is_active(run_id)
                ]
                if active:
                    raise ValueError("A live run is already active.")

            equity_by_symbol: dict[str, float] = {}
            if len(symbols) > 1 and account_equity is not None:
                allocation = float(account_equity) / len(symbols)
                equity_by_symbol = dict.fromkeys(symbols, allocation)
            elif len(symbols) > 1 and not effective_dry_run and not self.settings.kill_switch:
                equity_by_symbol = self._allocate_live_equity(symbols)

            if dry_run_override is not None:
                self.settings.dry_run = dry_run_override
            batch_id = str(uuid.uuid4())
            run_ids = [str(uuid.uuid4()) for _ in symbols]
            for run_id, symbol in zip(run_ids, symbols, strict=True):
                config: dict[str, Any] = {
                    "timeframes": timeframes,
                    "dashboard_batch_id": batch_id,
                }
                if symbol in equity_by_symbol:
                    config["account_equity"] = equity_by_symbol[symbol]
                elif account_equity is not None:
                    config["account_equity"] = float(account_equity)
                stop_event = threading.Event()
                self._stop_events[run_id] = stop_event
                thread = threading.Thread(
                    target=self._run,
                    args=(run_id, symbol, config, stop_event),
                    name=f"mats-run-{run_id[:8]}",
                    daemon=True,
                )
                self._threads[run_id] = thread
                thread.start()
        return {"run_ids": run_ids, "batch_id": batch_id}

    def stop(self, run_id: str) -> dict[str, str]:
        with self._lock:
            event = self._stop_events.get(run_id)
            if event is None or not self._run_is_active(run_id):
                raise ValueError(f"No active dashboard run found for run_id={run_id!r}.")
            self.settings.kill_switch = True
            event.set()
        return {
            "message": "Stop requested. The run or entry watcher will stop; "
            "the kill switch is now enabled."
        }

    def stop_all(self) -> dict[str, Any]:
        with self._lock:
            active_ids = [run_id for run_id, thread in self._threads.items() if thread.is_alive()]
            if not active_ids:
                raise ValueError("No active dashboard runs found.")
            self.settings.kill_switch = True
            for run_id in active_ids:
                event = self._stop_events.get(run_id)
                if event:
                    event.set()
        return {
            "run_ids": active_ids,
            "message": f"Stop requested for {len(active_ids)} run(s); kill switch enabled.",
        }

    def resume(
        self,
        run_id: str | None,
        *,
        account_equity: float | None = None,
        allow_parallel_live: bool = False,
    ) -> dict[str, str]:
        database = Database(self.settings.database_path)
        try:
            watcher = next(
                (
                    item
                    for item in database.entry_watchers()
                    if item["run_id"] == run_id
                    and item["status"] in {"stopped", "failed"}
                ),
                None,
            )
            if watcher is not None:
                with self._lock:
                    if self._run_is_active(str(watcher["run_id"])):
                        raise ValueError(f"Run {watcher['run_id']} is already active.")
                    database.start_entry_watcher(
                        str(watcher["run_id"]), int(watcher["interval_seconds"])
                    )
                    self._launch_entry_watcher(
                        str(watcher["run_id"]), int(watcher["interval_seconds"])
                    )
                return {
                    "run_id": str(watcher["run_id"]),
                    "message": "Entry watcher resume requested.",
                }
            row = database.resumable_run(run_id)
            if row is None:
                raise ValueError(f"No incomplete run found for run_id={run_id!r}.")
            selected_id = str(row["run_id"])
            saved_config = json.loads(row["config_snapshot"])
            equity = account_equity
            if equity is None:
                configured_equity = saved_config.get("account_equity")
                if not isinstance(configured_equity, bool) and isinstance(
                    configured_equity, int | float
                ):
                    equity = float(configured_equity)
            with self._lock:
                if self._run_is_active(selected_id):
                    raise ValueError(f"Run {selected_id} is already active.")
                active_live_run = (
                    not self.settings.dry_run
                    and not self.settings.kill_switch
                    and any(
                        run_id != selected_id and thread.is_alive()
                        for run_id, thread in self._threads.items()
                    )
                )
                if active_live_run and not allow_parallel_live:
                    raise ValueError("A live run is already active.")
                stop_event = threading.Event()
                self._stop_events[selected_id] = stop_event
                thread = threading.Thread(
                    target=self._resume,
                    args=(selected_id, stop_event, equity),
                    name=f"mats-resume-{selected_id[:8]}",
                    daemon=True,
                )
                self._threads[selected_id] = thread
                thread.start()
            return {"run_id": selected_id, "message": "Run resume requested."}
        finally:
            database.close()

    def resume_all(self) -> dict[str, Any]:
        snapshot = self.state()
        active = set(snapshot["active_run_ids"])
        candidates = [
            run
            for run in snapshot["runs"]
            if run["status"] in {"running", "failed", "stopped"} and run["run_id"] not in active
        ]
        watcher_ids = [
            str(watcher["run_id"])
            for watcher in snapshot["entry_watchers"]
            if watcher["status"] in {"stopped", "failed"}
            and watcher["run_id"] not in active
        ]
        if not candidates and not watcher_ids:
            raise ValueError("No inactive incomplete runs or stopped entry watchers found.")
        equity_overrides: dict[str, float] = {}
        if candidates and not self.settings.dry_run and not self.settings.kill_switch:
            if active:
                raise ValueError("Stop active live runs before resuming another live batch.")
            has_unallocated_runs = any(
                isinstance(run["config"].get("account_equity"), bool)
                or not isinstance(run["config"].get("account_equity"), int | float)
                for run in candidates
            )
            if len(candidates) > 1 and has_unallocated_runs:
                allocations = self._allocate_live_equity([run["symbol"] for run in candidates])
                equity_overrides = {run["run_id"]: allocations[run["symbol"]] for run in candidates}
        resumed = [
            self.resume(
                run["run_id"],
                account_equity=equity_overrides.get(
                    run["run_id"],
                    run["config"].get("account_equity")
                    if isinstance(run["config"].get("account_equity"), int | float)
                    else None,
                ),
                allow_parallel_live=True,
            )
            for run in candidates
        ]
        for watcher_id in watcher_ids:
            self.resume(watcher_id, allow_parallel_live=True)
        return {
            "run_ids": [item["run_id"] for item in resumed] + watcher_ids,
            "message": f"Resume requested for {len(resumed) + len(watcher_ids)} run(s).",
        }

    def toggle(self, name: str, enabled: Any, confirmed: Any) -> dict[str, Any]:
        if name not in {"dry_run", "kill_switch"}:
            raise ValueError(f"Unsupported setting: {name!r}.")
        if enabled is False and confirmed is not True:
            raise ValueError(f"Confirmation is required to disable {name.replace('_', ' ')}.")
        with self._lock:
            self._set_toggle(name, enabled)
        return {"settings": {name: getattr(self.settings, name)}}

    def monitor(self, run_ids: list[str] | None = None) -> dict[str, str]:
        operation_id = str(uuid.uuid4())
        self._start_operation(operation_id, "monitor")

        def execute() -> None:
            worker = None
            try:
                worker = TradingFramework(settings=self.settings)
                if run_ids:
                    results = worker.position_monitor.monitor_positions(run_ids)
                else:
                    results = worker.monitor_open_positions()
                worker.database.log_event(
                    None,
                    "monitor_cycle_completed",
                    f"Monitor cycle completed ({len(results)} positions).",
                )
                self._finish_operation(operation_id, "completed")
            except Exception as exc:
                if worker:
                    worker.database.log_event(None, "monitor_cycle_failed", str(exc), error=exc)
                self._finish_operation(operation_id, "failed", str(exc))
            finally:
                if worker:
                    worker.close()

        threading.Thread(
            target=execute, name=f"mats-monitor-{operation_id[:8]}", daemon=True
        ).start()
        return {"operation_id": operation_id, "message": "Monitor cycle started."}

    def flatten(self, run_ids: list[str], confirmed: Any) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("Confirmation is required before flattening positions.")
        worker = TradingFramework(settings=self.settings)
        try:
            results = worker.position_monitor.flatten(run_ids)
            return {"results": results}
        finally:
            worker.close()

    def _run(
        self,
        run_id: str,
        symbol: str,
        config: dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        worker = None
        try:
            worker = TradingFramework(settings=self.settings, stop_event=stop_event)
            result = worker.run(symbol, config, run_id=run_id)
            self._maybe_start_entry_watcher(run_id, result, stop_event)
        except Exception as exc:
            if worker is None:
                database = None
                try:
                    database = Database(self.settings.database_path)
                    self._record_run_start_failure(database, run_id, symbol, config, exc)
                except Exception:
                    logger.exception("Could not initialize recovery for dashboard run %s", run_id)
                finally:
                    if database:
                        database.close()
            else:
                self._record_run_start_failure(worker.database, run_id, symbol, config, exc)
        finally:
            if worker:
                worker.close()

    def _record_run_start_failure(
        self,
        database: Database,
        run_id: str,
        symbol: str,
        config: dict[str, Any],
        error: Exception,
    ) -> None:
        try:
            if not database.run_exists(run_id):
                database.create_run(
                    run_id,
                    symbol,
                    {
                        "timeframes_to_consider": config.get(
                            "timeframes", self.settings.timeframes
                        ),
                        "account_equity": config.get("account_equity"),
                        "dashboard_batch_id": config.get("dashboard_batch_id"),
                    },
                )
            database.log_event(run_id, "dashboard_run_failed", str(error), error=error)
            database.update_run_status(run_id, "failed")
        except Exception:
            logger.exception("Could not persist startup failure for dashboard run %s", run_id)

    def _resume(
        self, run_id: str, stop_event: threading.Event, account_equity: float | None = None
    ) -> None:
        worker = None
        try:
            worker = TradingFramework(settings=self.settings, stop_event=stop_event)
            result = worker.resume(run_id, account_equity=account_equity)
            self._maybe_start_entry_watcher(run_id, result, stop_event)
        except Exception as exc:
            if worker:
                worker.database.log_event(run_id, "dashboard_resume_failed", str(exc), error=exc)
                worker.database.update_run_status(run_id, "failed")
        finally:
            if worker:
                worker.close()

    def _maybe_start_entry_watcher(
        self, run_id: str, state: GraphState, stop_event: threading.Event
    ) -> None:
        if (
            state.status not in {"running", "completed"}
            or state.critique is None
            or state.critique.decision != "APPROVE"
            or state.strategy is None
            or stop_event.is_set()
        ):
            return
        with self._lock:
            if self.settings.dry_run or self.settings.kill_switch or stop_event.is_set():
                return
            database = Database(self.settings.database_path)
            try:
                if any(
                    position["run_id"] == run_id
                    for position in database.open_positions(state.symbol)
                ):
                    return
                database.start_entry_watcher(run_id, _ENTRY_WATCH_INTERVAL_SECONDS)
            finally:
                database.close()
            self._launch_entry_watcher(run_id, _ENTRY_WATCH_INTERVAL_SECONDS)

    def _launch_entry_watcher(self, run_id: str, interval_seconds: int) -> None:
        watcher_stop_event = threading.Event()
        with self._lock:
            self._stop_events[run_id] = watcher_stop_event
            thread = threading.Thread(
                target=self._watch_entries,
                args=(run_id, watcher_stop_event, interval_seconds),
                name=f"mats-entry-watch-{run_id[:8]}",
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()

    def _restore_entry_watchers(self) -> None:
        if not self.settings.database_path.exists():
            return
        database = Database(self.settings.database_path)
        try:
            watchers = database.entry_watchers(active_only=True)
        finally:
            database.close()
        for watcher in watchers:
            run_id = str(watcher["run_id"])
            stop_event = threading.Event()
            with self._lock:
                self._stop_events[run_id] = stop_event
                thread = threading.Thread(
                    target=self._watch_entries,
                    args=(run_id, stop_event, int(watcher["interval_seconds"])),
                    name=f"mats-entry-watch-{run_id[:8]}",
                    daemon=True,
                )
                self._threads[run_id] = thread
                thread.start()

    def _watch_entries(
        self, run_id: str, stop_event: threading.Event, interval_seconds: int
    ) -> None:
        worker = None
        database = None
        try:
            worker = TradingFramework(settings=self.settings, stop_event=stop_event)
            database = worker.database
            raw_state = database.latest_checkpoint(run_id)
            if raw_state is None:
                raise ValueError(f"No saved graph state is available for watcher run {run_id}.")
            state = GraphState.model_validate(raw_state)
            if (
                state.critique is None
                or state.critique.decision != "APPROVE"
                or state.strategy is None
            ):
                raise ValueError(f"Watcher run {run_id} has no approved strategy to monitor.")

            while not stop_event.is_set():
                if self.settings.dry_run or self.settings.kill_switch:
                    database.update_entry_watcher(run_id, status="paused")
                    stop_event.wait(interval_seconds)
                    continue
                try:
                    risk_update = RiskAssessmentNode(worker.context)(state)
                    state = state.model_copy(update=risk_update)
                    signal = state.live_signal
                    if signal is None:
                        raise RuntimeError("Risk assessment returned no live signal.")
                    if (
                        signal.action in {"long", "short"}
                        and state.risk_assessment is not None
                        and state.risk_assessment.approved
                        and not stop_event.is_set()
                        and not self.settings.dry_run
                        and not self.settings.kill_switch
                    ):
                        execution_update = ExecutionNode(worker.context)(state)
                        state = state.model_copy(update=execution_update)
                    database.update_entry_watcher(
                        run_id,
                        status="watching",
                        signal=signal.model_dump(mode="json"),
                        error=None,
                    )
                    if any(position["run_id"] == run_id for position in database.open_positions()):
                        database.update_entry_watcher(run_id, status="triggered")
                        database.log_event(
                            run_id,
                            "entry_watcher_triggered",
                            "The entry watcher placed an order and saved an open position.",
                        )
                        return
                except Exception as exc:
                    database.update_entry_watcher(run_id, status="watching", error=str(exc))
                    database.log_event(run_id, "entry_watcher_check_failed", str(exc), error=exc)
                    logger.exception("Entry watcher check failed for run %s", run_id)
                stop_event.wait(interval_seconds)
            database.update_entry_watcher(run_id, status="stopped")
            database.log_event(run_id, "entry_watcher_stopped", "Entry watcher stopped.")
        except Exception as exc:
            if database is None and worker is not None:
                database = worker.database
            if database is not None:
                database.update_entry_watcher(run_id, status="failed", error=str(exc))
                database.log_event(run_id, "entry_watcher_failed", str(exc), error=exc)
            logger.exception("Entry watcher could not start for run %s", run_id)
        finally:
            if worker:
                worker.close()

    def _run_is_active(self, run_id: str) -> bool:
        thread = self._threads.get(run_id)
        return bool(thread and thread.is_alive())

    def _allocate_live_equity(self, symbols: list[str]) -> dict[str, float]:
        balance = ExecutionTools(self.settings).get_balance()
        symbols_by_quote: dict[str, list[str]] = {}
        for symbol in symbols:
            quote = symbol.split("/")[-1]
            symbols_by_quote.setdefault(quote, []).append(symbol)
        allocations: dict[str, float] = {}
        for quote, quote_symbols in symbols_by_quote.items():
            available = balance.free.get(quote)
            if available is None:
                available = balance.currencies.get(quote, {}).get("free")
            if available is None:
                raise ValueError(
                    f"No available {quote} balance to allocate across the selected live runs."
                )
            available_equity = float(available)
            if not math.isfinite(available_equity) or available_equity <= 0:
                raise ValueError(
                    f"No available {quote} balance to allocate across the selected live runs."
                )
            allocation = available_equity / len(quote_symbols)
            allocations.update(dict.fromkeys(quote_symbols, allocation))
        return allocations

    def _set_toggle(self, name: str, enabled: Any) -> None:
        if not isinstance(enabled, bool):
            raise ValueError(f"{name} must be a boolean.")
        setattr(self.settings, name, enabled)

    def _start_operation(self, operation_id: str, name: str) -> None:
        with self._lock:
            self._operations[operation_id] = {
                "operation_id": operation_id,
                "name": name,
                "status": "running",
            }

    def _finish_operation(self, operation_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            operation = self._operations[operation_id]
            operation["status"] = status
            if error:
                operation["error"] = error


def _read_snapshot(database_path: Path) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "runs": [],
        "positions": [],
        "system_events": [],
        "entry_watchers": [],
    }
    if not database_path.exists():
        return empty
    connection = sqlite3.connect(database_path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        runs = [
            dict(row)
            for row in connection.execute(
                """SELECT run_id, symbol, status, started_at, finished_at, config_snapshot
               FROM runs ORDER BY started_at DESC"""
            )
        ]
        steps = []
        for row in connection.execute(
            """SELECT step_id, run_id, agent_name, input_json, output_json, tool_calls_json,
                          status, error, tokens_used, cost, started_at, finished_at
                   FROM agent_steps ORDER BY started_at"""
        ):
            step = dict(row)
            step["input"] = json.loads(step.pop("input_json"))
            output = step.pop("output_json")
            step["output"] = json.loads(output) if output else None
            step["tool_calls"] = json.loads(step.pop("tool_calls_json"))
            steps.append(step)
        strategies = [
            dict(row)
            for row in connection.execute(
                """SELECT s.strategy_id, s.run_id, s.version, s.definition_json, b.backtest_id,
                          b.report_json, b.equity_curve_json, b.costs_json, c.decision,
                          c.issues_json, c.revision_instructions
                   FROM strategies s
                   LEFT JOIN backtests b ON b.strategy_id = s.strategy_id
                   LEFT JOIN critiques c ON c.backtest_id = b.backtest_id
                   ORDER BY s.run_id, s.version"""
            )
        ]
        for item in strategies:
            for key, source in (
                ("strategy", "definition_json"),
                ("report", "report_json"),
                ("equity_curve", "equity_curve_json"),
                ("costs", "costs_json"),
                ("issues", "issues_json"),
            ):
                item[key] = json.loads(item[source]) if item.get(source) else None
        executions = [
            {**dict(row), "orders": json.loads(row["orders_json"])}
            for row in connection.execute(
                "SELECT execution_id, run_id, orders_json, status FROM executions"
            )
        ]
        positions = [
            {
                **dict(row),
                "strategy": json.loads(row["strategy_json"]),
                "events": json.loads(row["events_json"]),
            }
            for row in connection.execute("SELECT * FROM live_positions ORDER BY opened_at DESC")
        ]
        system_events = [
            dict(row)
            for row in connection.execute(
                """SELECT event_id, run_id, type, message, traceback, timestamp
                   FROM system_events ORDER BY timestamp DESC LIMIT 200"""
            )
        ]
        entry_watchers = [
            {
                **dict(row),
                "signal": json.loads(row["signal_json"]) if row["signal_json"] else None,
            }
            for row in connection.execute("SELECT * FROM entry_watchers ORDER BY created_at")
        ]
        return {
            "runs": [
                {
                    **{key: value for key, value in run.items() if key != "config_snapshot"},
                    "config": json.loads(run["config_snapshot"]),
                    "steps": [step for step in steps if step["run_id"] == run["run_id"]],
                    "strategies": [item for item in strategies if item["run_id"] == run["run_id"]],
                    "executions": [item for item in executions if item["run_id"] == run["run_id"]],
                    "events": [item for item in system_events if item["run_id"] == run["run_id"]],
                }
                for run in runs
            ],
            "positions": positions,
            "system_events": system_events,
            "entry_watchers": entry_watchers,
        }
    finally:
        connection.close()


def _symbols(value: Any, defaults: list[str]) -> list[str]:
    symbols = defaults if value is None else value
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol) for symbol in symbols)
    ):
        raise ValueError("symbols must be a non-empty list of pairs like BTC/USDT.")
    if len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be unique.")
    return symbols


def _timeframes(value: Any, defaults: list[str]) -> list[str]:
    timeframes = defaults if value is None else value
    if (
        not isinstance(timeframes, list)
        or not timeframes
        or any(item not in _ALLOWED_TIMEFRAMES for item in timeframes)
    ):
        raise ValueError("timeframes contains an unsupported timeframe.")
    return timeframes


class DashboardHandler(BaseHTTPRequestHandler):
    service: DashboardService

    def __init__(self, *args: Any, service: DashboardService, **kwargs: Any):
        self.service = service
        super().__init__(*args, **kwargs)

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self._json_response(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed."})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._request_allowed():
            return
        route = urlsplit(self.path).path
        try:
            if route == "/api/state":
                self._json_response(HTTPStatus.OK, self.service.state())
            elif route == "/api/events":
                self._events()
            else:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        except ValueError as exc:
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Dashboard GET request failed for %s", route)
            self._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)},
            )

    def do_POST(self) -> None:
        if not self._request_allowed():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64_000:
                raise ValueError("Request body must be between 1 and 64000 bytes.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
            response = self._dispatch(urlsplit(self.path).path, payload)
            self._json_response(HTTPStatus.ACCEPTED, response)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except (OSError, RuntimeError) as exc:
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _dispatch(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        if route != "/api/actions":
            raise KeyError("Not found.")
        action = payload.get("action")
        if action == "start":
            return self.service.start(payload)
        if action == "stop":
            return self.service.stop(_required_string(payload, "run_id"))
        if action == "stop_all":
            return self.service.stop_all()
        if action == "resume":
            run_id = payload.get("run_id")
            if run_id is not None and not isinstance(run_id, str):
                raise ValueError("run_id must be a string.")
            return self.service.resume(run_id)
        if action == "resume_all":
            return self.service.resume_all()
        if action == "monitor":
            run_ids = payload.get("run_ids")
            if run_ids is not None and (
                not isinstance(run_ids, list) or any(not isinstance(item, str) for item in run_ids)
            ):
                raise ValueError("run_ids must be a list of strings.")
            return self.service.monitor(run_ids)
        if action == "flatten":
            run_ids = payload.get("run_ids")
            if not isinstance(run_ids, list) or any(not isinstance(item, str) for item in run_ids):
                raise ValueError("run_ids must be a list of strings.")
            return self.service.flatten(run_ids, payload.get("confirmed"))
        if action == "toggle":
            return self.service.toggle(
                _required_string(payload, "name"), payload.get("enabled"), payload.get("confirmed")
            )
        raise ValueError(f"Unsupported action: {action!r}.")

    def _events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        previous = ""
        try:
            while True:
                data = self.service.state()
                serialized = json.dumps(data, separators=(",", ":"), sort_keys=True)
                digest = hashlib.sha256(serialized.encode()).hexdigest()
                if digest != previous:
                    self.wfile.write(f"data: {serialized}\n\n".encode())
                    self.wfile.flush()
                    previous = digest
                else:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _request_allowed(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"localhost", "127.0.0.1"}:
            self._json_response(HTTPStatus.FORBIDDEN, {"error": "Localhost requests only."})
            return False
        if not self._origin_allowed():
            self._json_response(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed."})
            return False
        return True

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in _ALLOWED_ORIGINS

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in _ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json_response(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    return value


class DashboardHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: tuple[str, int]) -> None:
        if isinstance(
            sys.exception(),
            BrokenPipeError | ConnectionAbortedError | ConnectionResetError,
        ):
            return
        super().handle_error(request, client_address)


def main() -> None:
    service = DashboardService()

    class BoundDashboardHandler(DashboardHandler):
        def __init__(self, *args: Any, **kwargs: Any):
            super().__init__(*args, service=service, **kwargs)

    server = DashboardHTTPServer(("127.0.0.1", 8765), BoundDashboardHandler)
    server.daemon_threads = True
    print("MATS dashboard API listening on http://127.0.0.1:8765")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
