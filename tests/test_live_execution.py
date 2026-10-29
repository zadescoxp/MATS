import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from framework.agents.execution import ExecutionNode
from framework.agents.position_monitor import PositionMonitor
from framework.config.settings import Settings
from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.persistence.database import Database
from framework.schemas.models import (
    Critique,
    OHLCVBar,
    RiskAssessment,
    StrategyDefinition,
)
from framework.tools.execution import ExecutionTools


class Market:
    def __init__(self, candles: list[OHLCVBar]) -> None:
        self.candles = candles

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int):
        return type("OHLCV", (), {"candles": self.candles[-limit:]})()


class Exchange:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.order_count = 0

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.order_count += 1
        order = {
            "id": f"exchange-{self.order_count}",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "status": "open" if "triggerPrice" in params else "closed",
            "amount": amount,
            "filled": 0 if "triggerPrice" in params else amount,
            "remaining": 0,
            "average": 120,
            "price": price,
            "info": params,
        }
        self.orders.append(order)
        return order

    def cancel_order(self, order_id, symbol):
        return {"id": order_id, "symbol": symbol, "status": "canceled"}

    def fetch_order(self, order_id, symbol, params=None):
        return next(order for order in self.orders if order["id"] == order_id)


def test_execution_places_and_persists_testnet_order_and_protective_stop() -> None:
    settings, database, market, execution = _setup()
    try:
        database.create_run("run-entry", "BTC/USDT", {})
        state = _approved_state("run-entry", market.candles)
        result = ExecutionNode(_context(settings, database, market, execution))(state)
        report = result["execution_result"]
        assert report.status == "filled"
        assert report.live_signal and report.live_signal.action == "long"
        assert "Fast EMA crossed above slow EMA" in report.execution_result
        assert "exchange_order_id" in report.orders[0]
        assert report.orders[0]["exchange_order_id"] == "exchange-1"
        assert report.orders[1]["exchange_order_id"] == "exchange-2"

        saved = database.connection.execute(
            "SELECT orders_json FROM executions WHERE run_id = ?", ("run-entry",)
        ).fetchone()
        orders = json.loads(saved["orders_json"])
        assert orders[0]["id"] == "exchange-1"
        assert orders[0]["exchange_order_id"] == "exchange-1"
        position = database.open_positions("BTC/USDT")[0]
        assert position["entry_order_id"] == "exchange-1"
        assert position["protective_order_id"] == "exchange-2"
    finally:
        database.close()


def test_monitor_flattens_stopped_strategy_and_prevents_reentry() -> None:
    settings, database, market, execution = _setup()
    try:
        database.create_run("run-monitor", "BTC/USDT", {})
        entry_state = _approved_state("run-monitor", market.candles)
        context = _context(settings, database, market, execution)
        node_result = ExecutionNode(context)(entry_state)
        assert node_result["execution_result"].status == "filled"
        entry = market.candles[-1]
        market.candles = [
            *market.candles,
            _bar(entry.timestamp + timedelta(hours=1), 115, low=114),
        ]

        decisions = PositionMonitor(context)()
        assert decisions[0]["action"] == "strategy_stopped"
        assert decisions[0]["exchange_order_ids"] == ["exchange-3"]
        position = database.open_positions("BTC/USDT")
        assert position == []
        assert database.strategy_stop_reason(
            "BTC/USDT",
            database.connection.execute(
                "SELECT strategy_key FROM live_positions WHERE run_id = ?",
                ("run-monitor",),
            ).fetchone()["strategy_key"],
        )
        run = database.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", ("run-monitor",)
        ).fetchone()
        assert run["status"] == "stopped"

        database.create_run("run-resume", "BTC/USDT", {})
        resumed_state = _approved_state("run-resume", market.candles)
        resumed = ExecutionNode(context)(resumed_state)
        assert resumed["execution_result"].status == "hold"
        assert "previously stopped" in resumed["execution_result"].execution_result
        assert execution.exchange.order_count == 3
    finally:
        database.close()


def test_edge_fade_expectancy_forces_flatten() -> None:
    settings, database, market, execution = _setup()
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-prior", "BTC/USDT", {})
        ExecutionNode(context)(_approved_state("run-prior", market.candles))
        prior = database.open_positions("BTC/USDT")[0]
        database.update_live_position(
            "run-prior",
            status="closed",
            event_type="test_close",
            reason="negative prior result",
            bars_held=1,
            net_pnl=-0.5,
        )

        database.create_run("run-edge-fade", "BTC/USDT", {})
        ExecutionNode(context)(_approved_state("run-edge-fade", market.candles))
        decision = PositionMonitor(context)()[0]
        assert decision["action"] == "edge_fade_exit"
        assert "edge faded" in decision["reason"]
        assert database.open_positions("BTC/USDT") == []
        assert database.strategy_stop_reason("BTC/USDT", prior["strategy_key"])
        assert execution.exchange.order_count == 5
    finally:
        database.close()


def test_manual_flatten_cancels_protective_order_and_closes_selected_position() -> None:
    settings, database, market, execution = _setup()
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-manual-flatten", "BTC/USDT", {})
        ExecutionNode(context)(_approved_state("run-manual-flatten", market.candles))

        result = PositionMonitor(context).flatten(["run-manual-flatten"])

        assert result[0]["action"] == "manual_flatten"
        assert database.open_positions("BTC/USDT") == []
        assert execution.exchange.order_count == 3
        assert (
            database.connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", ("run-manual-flatten",)
            ).fetchone()["status"]
            == "stopped"
        )
    finally:
        database.close()


def test_manual_flatten_refuses_to_place_an_order_in_dry_run() -> None:
    settings, database, market, execution = _setup()
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-dry-flatten", "BTC/USDT", {})
        ExecutionNode(context)(_approved_state("run-dry-flatten", market.candles))
        order_count = execution.exchange.order_count
        settings.dry_run = True

        with pytest.raises(ValueError, match="Disable dry-run"):
            PositionMonitor(context).flatten(["run-dry-flatten"])

        assert execution.exchange.order_count == order_count
        assert len(database.open_positions("BTC/USDT")) == 1
    finally:
        database.close()


def _setup():
    settings = Settings(
        database_path=Path(":memory:"),
        bybit_api_key=SecretStr("test-key"),
        bybit_api_secret=SecretStr("test-secret"),
        bybit_testnet=True,
        dry_run=False,
        kill_switch=False,
        edge_fade_lookback=3,
    )
    database = Database(":memory:")
    candles = [
        _bar(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index), price)
        for index, price in enumerate([100] * 30 + [100, 98, 96, 94, 92, 90, 120])
    ]
    market = Market(candles)
    exchange = Exchange()
    execution = ExecutionTools(settings, exchange=exchange)
    return settings, database, market, execution


def _context(settings, database, market, execution):
    return AgentContext(
        settings=settings,
        database=database,
        llm=None,
        market_data=market,
        backtester=None,
        execution=execution,
    )


def _approved_state(run_id: str, candles: list[OHLCVBar]) -> GraphState:
    strategy = StrategyDefinition(
        name="EMA",
        description="EMA crossover fixture",
        chosen_timeframe="1h",
        template_name="ema_crossover",
        entry_rule="ema_crossover",
        fast_period=3,
        slow_period=8,
        stop_loss_pct=0.02,
    )
    return GraphState(
        run_id=run_id,
        symbol="BTC/USDT",
        timeframes_to_consider=["1h"],
        raw_data={"1h": candles},
        strategy=strategy,
        critique=Critique(decision="APPROVE", rationale="Approved fixture."),
        risk_assessment=RiskAssessment(
            approved=True,
            side="long",
            position_size=0.1,
            position_notional=12,
            risk_amount=0.24,
            max_concurrent_exposure=0.0012,
            stop_price=117.6,
        ),
    )


def _bar(timestamp: datetime, close: float, *, low: float | None = None) -> OHLCVBar:
    return OHLCVBar(
        timestamp=timestamp,
        open=close,
        high=close + 0.2,
        low=low if low is not None else close - 0.2,
        close=close,
        volume=10,
    )
