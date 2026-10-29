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
        database.start_entry_watcher("run-1", 60)
        database.update_entry_watcher(
            "run-1",
            signal={"action": "hold", "reason": "No entry yet."},
        )
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
    assert snapshot["entry_watchers"][0]["run_id"] == "run-1"
    assert snapshot["entry_watchers"][0]["signal"]["action"] == "hold"


def test_dashboard_registers_entry_watcher_only_for_live_approved_runs(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(database_path=tmp_path / "state.db", dry_run=False, kill_switch=False)
    database = Database(settings.database_path)
    database.create_run("run-live", "BTC/USDT", {})
    database.close()
    service = DashboardService(settings)
    threads = _capture_threads(monkeypatch)
    state = SimpleNamespace(
        status="running",
        symbol="BTC/USDT",
        critique=SimpleNamespace(decision="APPROVE"),
        strategy=object(),
    )

    service._maybe_start_entry_watcher("run-live", state, Event())

    watcher = Database(settings.database_path)
    try:
        saved = watcher.entry_watchers()
    finally:
        watcher.close()
    assert saved[0]["status"] == "watching"
    assert saved[0]["interval_seconds"] == 60
    assert threads[0].name == "mats-entry-watch-run-live"


def test_dashboard_restores_persisted_entry_watcher(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.db"
    database = Database(path)
    database.create_run("run-watch", "BTC/USDT", {})
    database.start_entry_watcher("run-watch", 60)
    database.close()
    threads = _capture_threads(monkeypatch)

    service = DashboardService(Settings(database_path=path))

    assert threads[0].name == "mats-entry-watch-run-watc"
    assert service.state()["active_run_ids"] == ["run-watch"]


def test_dashboard_resumes_stopped_entry_watcher(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.db"
    database = Database(path)
    database.create_run("run-watch", "BTC/USDT", {})
    database.start_entry_watcher("run-watch", 60)
    database.update_entry_watcher("run-watch", status="stopped")
    database.close()
    service = DashboardService(Settings(database_path=path))
    threads = _capture_threads(monkeypatch)

    response = service.resume("run-watch")

    assert response["message"] == "Entry watcher resume requested."
    assert threads[0].name == "mats-entry-watch-run-watc"


def test_entry_watcher_rechecks_risk_and_execution_for_actionable_signal(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "state.db"
    settings = Settings(database_path=path, dry_run=False, kill_switch=False)
    service = DashboardService(settings)
    database = Database(path)
    database.create_run("run-watch", "BTC/USDT", {})
    database.start_entry_watcher("run-watch", 60)
    database.close()
    stop_event = Event()
    calls: list[str] = []

    class WatchState:
        critique = SimpleNamespace(decision="APPROVE")
        strategy = object()

        def model_copy(self, update):
            copied = WatchState()
            for key, value in self.__dict__.items():
                setattr(copied, key, value)
            for key, value in update.items():
                setattr(copied, key, value)
            return copied

    state = WatchState()

    class FakeDatabase:
        def latest_checkpoint(self, _run_id):
            return {}

        def update_entry_watcher(self, *_args, **_kwargs):
            pass

        def open_positions(self):
            return []

        def log_event(self, *_args, **_kwargs):
            pass

    class FakeWorker:
        def __init__(self, **_kwargs):
            self.database = FakeDatabase()
            self.context = object()

        def close(self):
            pass

    class FakeSignal:
        action = "long"

        def model_dump(self, **_kwargs):
            return {"action": self.action, "reason": "Entry condition met."}

    class FakeRiskAssessment:
        def __init__(self, _context):
            pass

        def __call__(self, _state):
            calls.append("risk")
            return {
                "risk_assessment": SimpleNamespace(approved=True),
                "live_signal": FakeSignal(),
            }

    class FakeExecution:
        def __init__(self, _context):
            pass

        def __call__(self, _state):
            calls.append("execution")
            stop_event.set()
            return {}

    monkeypatch.setattr("framework.web.TradingFramework", FakeWorker)
    monkeypatch.setattr(
        "framework.web.GraphState.model_validate", classmethod(lambda _cls, _value: state)
    )
    monkeypatch.setattr("framework.web.RiskAssessmentNode", FakeRiskAssessment)
    monkeypatch.setattr("framework.web.ExecutionNode", FakeExecution)

    service._watch_entries("run-watch", stop_event, 60)

    assert calls == ["risk", "execution"]


@pytest.mark.parametrize(
    ("dry_run", "kill_switch"),
    [(True, False), (False, True)],
)
def test_dashboard_does_not_start_entry_watcher_with_safety_switches_enabled(
    tmp_path: Path, monkeypatch, dry_run: bool, kill_switch: bool
) -> None:
    settings = Settings(
        database_path=tmp_path / "state.db",
        dry_run=dry_run,
        kill_switch=kill_switch,
    )
    service = DashboardService(settings)
    threads = _capture_threads(monkeypatch)
    state = SimpleNamespace(
        status="completed",
        critique=SimpleNamespace(decision="APPROVE"),
        strategy=object(),
    )

    service._maybe_start_entry_watcher("run-live", state, Event())

    assert threads == []
    assert service.state()["entry_watchers"] == []


def test_dashboard_market_data_uses_backend_market_adapter(tmp_path: Path, monkeypatch) -> None:
    candle = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "timestamp": "2026-10-30T00:00:00Z",
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 2,
            "volume": 3,
        }
    )

    class FakeMarketDataTools:
        def __init__(self, _settings):
            pass

        def fetch_ohlcv(self, symbol, timeframe, limit):
            assert (symbol, timeframe, limit) == ("BTC/USDT", "1m", 300)
            return SimpleNamespace(symbol=symbol, timeframe=timeframe, candles=[candle])

    monkeypatch.setattr("framework.web.MarketDataTools", FakeMarketDataTools)
    service = DashboardService(Settings(database_path=tmp_path / "state.db"))

    snapshot = service.market_data("BTC/USDT", "1m")

    assert snapshot["candles"][0]["close"] == 2
    with pytest.raises(ValueError, match="pairs"):
        service.market_data("invalid", "1m")


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
    assert "kill switch is now enabled" in response["message"]


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


def test_market_http_endpoint_returns_backend_candles_and_validates_query(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeCandle:
        def model_dump(self, **_kwargs):
            return {
                "timestamp": "2026-10-30T00:00:00Z",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "volume": 3,
            }

    class FakeMarketDataTools:
        def __init__(self, _settings):
            pass

        def fetch_ohlcv(self, symbol, timeframe, limit):
            return SimpleNamespace(symbol=symbol, timeframe=timeframe, candles=[FakeCandle()])

    monkeypatch.setattr("framework.web.MarketDataTools", FakeMarketDataTools)
    service = DashboardService(Settings(database_path=tmp_path / "state.db"))

    class BoundHandler(DashboardHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, service=service, **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), BoundHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/api/market?symbol=BTC%2FUSDT&timeframe=1m") as response:
            snapshot = json.load(response)
        assert snapshot["symbol"] == "BTC/USDT"
        assert snapshot["candles"][0]["close"] == 2

        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/api/market?symbol=invalid&timeframe=1m")
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
