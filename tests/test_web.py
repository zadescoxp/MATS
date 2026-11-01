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


def test_snapshot_keeps_activity_events_scoped_to_their_run(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    database = Database(path)
    try:
        database.create_run("run-1", "BTC/USDT", {})
        database.create_run("run-2", "ETH/USDT", {})
        database.log_event("run-1", "algorithm_check", "BTC watcher check")
        database.log_event("run-2", "algorithm_check", "ETH watcher check")
    finally:
        database.close()

    runs = {run["run_id"]: run for run in _read_snapshot(path)["runs"]}

    assert [event["message"] for event in runs["run-1"]["events"]] == ["BTC watcher check"]
    assert [event["message"] for event in runs["run-2"]["events"]] == ["ETH watcher check"]


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


@pytest.mark.parametrize(
    ("previous_bar", "expected_calls", "expected_detail"),
    [
        (None, ["risk", "execution"], "New candle"),
        ("bar-1", ["risk"], "No new candle"),
    ],
)
def test_entry_watcher_checks_every_interval_without_repeating_same_bar_execution(
    tmp_path: Path, monkeypatch, previous_bar: str | None,
    expected_calls: list[str], expected_detail: str,
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
    logged_events: list[tuple[str, str]] = []

    class WatchState:
        symbol = "BTC/USDT"
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

        def entry_watchers(self):
            return [{"run_id": "run-watch", "last_bar_timestamp": previous_bar}]

        def update_entry_watcher(self, *_args, **_kwargs):
            stop_event.set()

        def open_positions(self):
            return []

        def strategy_stop_reason(self, *_args):
            return None

        def log_event(self, _run_id, event_type, message, **_kwargs):
            logged_events.append((event_type, message))

    class FakeMonitor:
        def __init__(self, _context):
            pass

        def edge_fade_reason(self, _key):
            return None

    class FakeWorker:
        def __init__(self, **_kwargs):
            self.database = FakeDatabase()
            self.context = object()

        def close(self):
            pass

    class FakeSignal:
        action = "long"
        price = 100.0
        reason = "Entry condition met."

        def model_dump(self, **_kwargs):
            return {"action": self.action, "price": self.price, "reason": self.reason}

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
    monkeypatch.setattr("framework.web._closed_bar_key", lambda *_args: "bar-1")
    monkeypatch.setattr(
        "framework.web.GraphState.model_validate", classmethod(lambda _cls, _value: state)
    )
    monkeypatch.setattr("framework.web.RiskAssessmentNode", FakeRiskAssessment)
    monkeypatch.setattr("framework.web.ExecutionNode", FakeExecution)
    monkeypatch.setattr("framework.web.PositionMonitor", FakeMonitor)
    monkeypatch.setattr("framework.web.strategy_fingerprint", lambda _strategy: "key-1")
    monkeypatch.setattr("framework.web._live_sentiment", lambda _symbol: None)

    service._watch_entries("run-watch", stop_event, 60)

    assert calls == expected_calls
    check_events = [event for event in logged_events if event[0] == "algorithm_check"]
    assert len(check_events) == 1
    assert "Minute check on bar bar-1" in check_events[0][1]
    assert "at 100" in check_events[0][1]
    assert expected_detail in check_events[0][1]


def test_dashboard_does_not_start_algorithm_in_dry_run_and_says_why(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(database_path=tmp_path / "state.db", dry_run=True, kill_switch=False)
    database = Database(settings.database_path)
    database.create_run("run-live", "BTC/USDT", {})
    database.close()
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
    database = Database(settings.database_path)
    try:
        events = database.connection.execute(
            "SELECT type FROM system_events WHERE run_id = 'run-live'"
        ).fetchall()
    finally:
        database.close()
    assert [row["type"] for row in events] == ["algorithm_not_started"]


def test_dashboard_starts_algorithm_paused_when_kill_switch_is_on(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(database_path=tmp_path / "state.db", dry_run=False, kill_switch=True)
    database = Database(settings.database_path)
    database.create_run("run-live", "BTC/USDT", {})
    database.close()
    service = DashboardService(settings)
    threads = _capture_threads(monkeypatch)
    state = SimpleNamespace(
        status="completed",
        critique=SimpleNamespace(decision="APPROVE"),
        strategy=object(),
    )

    service._maybe_start_entry_watcher("run-live", state, Event())

    assert threads[0].name == "mats-entry-watch-run-live"


def test_paused_reason_names_the_blocking_switch() -> None:
    from framework.web import _paused_reason

    assert "kill switch" in _paused_reason(Settings(dry_run=False, kill_switch=True))
    assert "dry-run" in _paused_reason(Settings(dry_run=True, kill_switch=False))
    assert _paused_reason(Settings(dry_run=False, kill_switch=False)) is None


def _algorithm_harness(monkeypatch, *, open_position: bool, news):
    from framework.strategies.news_risk import NewsRisk

    stop_event = Event()
    calls: list[str] = []
    events: list[tuple[str, str]] = []
    statuses: list[str | None] = []

    class State:
        symbol = "BTC/USDT"
        critique = SimpleNamespace(decision="APPROVE")
        strategy = object()
        live_signal = None
        risk_assessment = None

        def model_copy(self, update):
            copied = State()
            for key, value in update.items():
                setattr(copied, key, value)
            return copied

    class FakeDatabase:
        def latest_checkpoint(self, _run_id):
            return {}

        def entry_watchers(self):
            return [{"run_id": "run-algo", "last_bar_timestamp": None}]

        def update_entry_watcher(self, _run_id, *, status=None, **_kwargs):
            statuses.append(status)
            stop_event.set()

        def open_positions(self):
            return [{"run_id": "run-algo"}] if open_position else []

        def strategy_stop_reason(self, *_args):
            return None

        def log_event(self, _run_id, event_type, message, **_kwargs):
            events.append((event_type, message))

    class FakeWorker:
        def __init__(self, **_kwargs):
            self.database = FakeDatabase()
            self.context = object()

        def close(self):
            pass

    class FakeMonitor:
        def __init__(self, _context):
            pass

        def edge_fade_reason(self, _key):
            return None

        def monitor_positions(self, run_ids, *, external_exits=None):
            calls.append(f"monitor:{sorted((external_exits or {}).values())}")
            return [{"run_id": run_ids[0], "action": "hold", "reason": "Holding."}]

    signal = SimpleNamespace(
        action="long",
        price=100.0,
        reason="Entry condition met.",
        model_dump=lambda **_kwargs: {"action": "long"},
    )

    def fake_risk(_context):
        def assess(_state):
            calls.append("risk")
            return {"risk_assessment": SimpleNamespace(approved=True), "live_signal": signal}

        return assess

    def fake_execution(_context):
        def execute(_state):
            calls.append("execution")
            return {}

        return execute

    monkeypatch.setattr("framework.web.TradingFramework", FakeWorker)
    monkeypatch.setattr("framework.web._closed_bar_key", lambda *_args: "bar-1")
    monkeypatch.setattr(
        "framework.web.GraphState.model_validate", classmethod(lambda _cls, _value: State())
    )
    monkeypatch.setattr("framework.web.RiskAssessmentNode", fake_risk)
    monkeypatch.setattr("framework.web.ExecutionNode", fake_execution)
    monkeypatch.setattr("framework.web.PositionMonitor", FakeMonitor)
    monkeypatch.setattr("framework.web.strategy_fingerprint", lambda _strategy: "key-1")
    monkeypatch.setattr("framework.web.assess_news_risk", lambda _snapshot: news)
    monkeypatch.setattr("framework.web._live_sentiment", lambda _symbol: {})
    return stop_event, calls, events, statuses, NewsRisk


def test_algorithm_blocks_entry_on_news_risk(tmp_path: Path, monkeypatch) -> None:
    from framework.strategies.news_risk import NewsRisk

    news = NewsRisk(True, False, "Market-wide shock headline: exchange hacked")
    stop_event, calls, events, _statuses, _ = _algorithm_harness(
        monkeypatch, open_position=False, news=news
    )
    service = DashboardService(
        Settings(database_path=tmp_path / "state.db", dry_run=False, kill_switch=False)
    )

    service._watch_entries("run-algo", stop_event, 60)

    assert calls == ["risk"]
    check = next(message for kind, message in events if kind == "algorithm_check")
    assert "Entry blocked by news risk" in check


def test_algorithm_manages_open_position_and_passes_news_exit(
    tmp_path: Path, monkeypatch
) -> None:
    from framework.strategies.news_risk import NewsRisk

    news = NewsRisk(True, True, "Coin-specific shock headline: BTC custodian hacked")
    stop_event, calls, events, statuses, _ = _algorithm_harness(
        monkeypatch, open_position=True, news=news
    )
    service = DashboardService(
        Settings(database_path=tmp_path / "state.db", dry_run=False, kill_switch=False)
    )

    service._watch_entries("run-algo", stop_event, 60)

    assert len(calls) == 1 and calls[0].startswith("monitor:")
    assert "news_risk_exit" in calls[0]
    assert any(kind == "position_check" for kind, _message in events)
    assert statuses[0] == "in_position"


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


def test_stop_sets_stop_event_without_enabling_kill_switch(tmp_path: Path) -> None:
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
    assert settings.kill_switch is False
    assert "Stop all" in response["message"]


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


def test_start_passes_max_attempts_as_run_revision_limit(tmp_path: Path, monkeypatch) -> None:
    service = DashboardService(Settings(database_path=tmp_path / "state.db"))
    threads = _capture_threads(monkeypatch)

    service.start({"symbols": ["BTC/USDT"], "max_attempts": 5})

    assert threads[0].args[2]["max_revision_loops"] == 4
    with pytest.raises(ValueError, match="max_attempts"):
        service.start({"symbols": ["BTC/USDT"], "max_attempts": 0})


def test_rejected_run_resumes_only_with_extra_attempts(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state.db"
    database = Database(path)
    database.create_run("run-rejected", "BTC/USDT", {})
    database.update_run_status("run-rejected", "rejected")
    database.close()
    service = DashboardService(Settings(database_path=path))
    threads = _capture_threads(monkeypatch)

    with pytest.raises(ValueError, match="more strategy attempts"):
        service.resume("run-rejected")
    response = service.resume("run-rejected", extra_attempts=2)

    assert response["message"] == "Trying 2 more strategy attempt(s)."
    assert threads[0].args[3] == 2
