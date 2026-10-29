import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from framework.config.settings import Settings
from framework.persistence.database import Database, _enable_wal
from framework.web import (
    DashboardHandler,
    DashboardService,
    _read_snapshot,
    _symbols,
    _timeframes,
)


def _capture_threads(monkeypatch):
    threads = []

    class CapturedThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True
            threads.append(self)

        def is_alive(self):
            return self.started

    monkeypatch.setattr("framework.web.threading.Thread", CapturedThread)
    return threads


def test_snapshot_decodes_run_steps_backtests_executions_and_positions(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database = Database(path)
    try:
        database.create_run("run-1", "BTC/USDT", {"timeframes_to_consider": ["1h"]})
        database.start_step("step-1", "run-1", "data_worker", {"symbol": "BTC/USDT"})
        database.finish_step(
            "step-1",
            output={"finding": "up"},
            tool_calls=[{"name": "submit_findings", "arguments": {"finding": "up"}}],
            tokens_used=12,
        )
        database.save_strategy(
            "strategy-1",
            "run-1",
            {"name": "EMA", "template_name": "ema_crossover"},
            1,
        )
        database.save_backtest(
            "backtest-1",
            "strategy-1",
            {"net_pnl": 12.5},
            [{"equity": 1012.5}],
            {"total_fees": 1},
        )
        database.save_critique("critique-1", "backtest-1", "APPROVE", [], "")
        database.save_execution("execution-1", "run-1", [{"id": "dry_run"}], "dry_run")
    finally:
        database.close()

    snapshot = _read_snapshot(path)
    run = snapshot["runs"][0]
    assert run["run_id"] == "run-1"
    assert run["steps"][0]["input"] == {"symbol": "BTC/USDT"}
    assert run["steps"][0]["output"] == {"finding": "up"}
    assert run["steps"][0]["tool_calls"] == [
        {"name": "submit_findings", "arguments": {"finding": "up"}}
    ]
    assert run["strategies"][0]["report"] == {"net_pnl": 12.5}
    assert run["strategies"][0]["decision"] == "APPROVE"
    assert run["executions"][0]["orders"] == [{"id": "dry_run"}]


def test_database_adds_tool_call_column_to_existing_agent_steps(tmp_path: Path) -> None:
    path = tmp_path / "legacy-state.db"
    database = Database(path)
    database.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE agent_steps DROP COLUMN tool_calls_json")
        connection.commit()
    finally:
        connection.close()

    migrated = Database(path)
    try:
        columns = {
            row["name"] for row in migrated.connection.execute("PRAGMA table_info(agent_steps)")
        }
        assert "tool_calls_json" in columns
        assert migrated.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='agent_steps'"
        ).fetchone()["sql"]
    finally:
        migrated.close()


def test_database_retries_transient_wal_lock() -> None:
    class LockedOnceConnection:
        calls = 0

        def execute(self, _statement: str) -> None:
            self.calls += 1
            if self.calls == 1:
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error

    connection = LockedOnceConnection()

    _enable_wal(connection)

    assert connection.calls == 2


def test_dashboard_service_migrates_existing_database_before_reading(tmp_path: Path) -> None:
    path = tmp_path / "legacy-state.db"
    database = Database(path)
    database.create_run("run-1", "BTC/USDT", {})
    database.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE agent_steps DROP COLUMN tool_calls_json")
        connection.commit()
    finally:
        connection.close()

    service = DashboardService(Settings(database_path=path))

    assert service.state()["runs"][0]["run_id"] == "run-1"
    assert service.state()["runs"][0]["steps"] == []


def test_run_startup_failure_registers_failed_run_and_event(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.db"
    service = DashboardService(Settings(database_path=path))

    def fail_framework_startup(**_kwargs):
        raise RuntimeError("simulated framework constructor failure")

    monkeypatch.setattr("framework.web.TradingFramework", fail_framework_startup)
    service._run("run-startup-failed", "BTC/USDT", {"timeframes": ["1h"]}, Event())

    snapshot = _read_snapshot(path)
    run = next(run for run in snapshot["runs"] if run["run_id"] == "run-startup-failed")
    assert run["status"] == "failed"
    assert run["events"][0]["type"] == "dashboard_run_failed"
    assert run["events"][0]["message"] == "simulated framework constructor failure"


def test_start_live_multi_symbol_splits_total_equity_across_parallel_runs(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(database_path=tmp_path / "state.db", dry_run=True, kill_switch=False)
    service = DashboardService(settings)
    threads = _capture_threads(monkeypatch)

    result = service.start(
        {
            "symbols": ["BTC/USDT", "ETH/USDT", "BNB/USDT"],
            "dry_run": False,
            "account_equity": 9000,
        }
    )

    assert len(result["run_ids"]) == 3
    assert len(threads) == 3
    assert all(thread.started for thread in threads)
    assert [thread.args[2]["account_equity"] for thread in threads] == [3000, 3000, 3000]
    assert all(thread.args[2]["dashboard_batch_id"] == result["batch_id"] for thread in threads)
    assert settings.dry_run is False


def test_start_live_multi_symbol_splits_available_balance(tmp_path: Path, monkeypatch) -> None:
    service = DashboardService(
        Settings(database_path=tmp_path / "state.db", dry_run=True, kill_switch=False)
    )
    threads = _capture_threads(monkeypatch)

    class FakeExecutionTools:
        def __init__(self, _settings):
            pass

        def get_balance(self):
            return SimpleNamespace(free={"USDT": 9000}, currencies={})

    monkeypatch.setattr("framework.web.ExecutionTools", FakeExecutionTools)

    service.start({"symbols": ["BTC/USDT", "ETH/USDT", "BNB/USDT"], "dry_run": False})

    assert [thread.args[2]["account_equity"] for thread in threads] == [3000, 3000, 3000]


def test_stop_enables_kill_switch_and_sets_stop_event(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "state.db", dry_run=False, kill_switch=False)
    service = DashboardService(settings)
    event = Event()
    service._stop_events["run-1"] = event

    class ActiveThread:
        def is_alive(self) -> bool:
            return True

    service._threads["run-1"] = ActiveThread()
    response = service.stop("run-1")

    assert event.is_set()
    assert settings.kill_switch is True
    assert "next agent boundary" in response["message"]


def test_stop_all_requests_every_active_run(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "state.db", dry_run=False, kill_switch=False)
    service = DashboardService(settings)
    events = [Event(), Event()]

    class ActiveThread:
        def is_alive(self) -> bool:
            return True

    for index, event in enumerate(events):
        run_id = f"run-{index}"
        service._stop_events[run_id] = event
        service._threads[run_id] = ActiveThread()

    response = service.stop_all()

    assert response["run_ids"] == ["run-0", "run-1"]
    assert all(event.is_set() for event in events)
    assert settings.kill_switch is True


def test_resume_all_starts_parallel_live_runs_with_allocated_equity(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "state.db"
    database = Database(path)
    try:
        database.create_run("failed-1", "BTC/USDT", {})
        database.update_run_status("failed-1", "failed")
        database.create_run("failed-2", "ETH/USDT", {})
        database.update_run_status("failed-2", "failed")
    finally:
        database.close()
    service = DashboardService(Settings(database_path=path, dry_run=False, kill_switch=False))
    threads = _capture_threads(monkeypatch)

    class FakeExecutionTools:
        def __init__(self, _settings):
            pass

        def get_balance(self):
            return SimpleNamespace(free={"USDT": 8000}, currencies={})

    monkeypatch.setattr("framework.web.ExecutionTools", FakeExecutionTools)

    response = service.resume_all()

    assert set(response["run_ids"]) == {"failed-1", "failed-2"}
    assert len(threads) == 2
    assert {thread.args[2] for thread in threads} == {4000}


@pytest.mark.parametrize("symbols", [[], ["btc/usdt"], ["BTC/USDT", "BTC/USDT"]])
def test_symbol_validation_rejects_invalid_values(symbols: list[str]) -> None:
    with pytest.raises(ValueError):
        _symbols(symbols, ["BTC/USDT"])


def test_timeframe_validation_rejects_unsupported_value() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _timeframes(["2d"], ["1h"])


def test_local_http_api_exposes_state_and_confirmed_setting_action(tmp_path: Path) -> None:
    service = DashboardService(
        Settings(
            database_path=tmp_path / "state.db",
            dry_run=True,
            kill_switch=True,
            symbols=["BTC/USDT"],
            timeframes=["1h"],
        )
    )

    class BoundHandler(DashboardHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, service=service, **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), BoundHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/api/state") as response:
            snapshot = json.load(response)
        assert snapshot["runs"] == []
        assert snapshot["settings"]["dry_run"] is True
        assert snapshot["active_run_ids"] == []

        request = Request(
            f"{base_url}/api/actions",
            data=json.dumps(
                {"action": "toggle", "name": "dry_run", "enabled": False, "confirmed": True}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            result = json.load(response)
        assert result["settings"]["dry_run"] is False
        assert service.settings.dry_run is False

        blocked_request = Request(
            f"{base_url}/api/state",
            headers={"Origin": "https://example.invalid"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(blocked_request)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
