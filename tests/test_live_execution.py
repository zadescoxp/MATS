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
    def __init__(self, free: dict[str, float] | None = None) -> None:
        self.orders: list[dict] = []
        self.order_count = 0
        self.free = free if free is not None else {"USDT": 100_000.0, "BTC": 10.0}

    def fetch_balance(self):
        return {"free": dict(self.free), "used": {}, "total": dict(self.free), "info": {}}

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


def test_monitor_closes_position_records_trade_and_rearms_strategy() -> None:
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
        assert decisions[0]["action"] == "position_closed"
        assert decisions[0]["exchange_order_ids"] == ["exchange-3"]
        assert database.open_positions("BTC/USDT") == []
        trades = database.strategy_trades("run-monitor")
        assert len(trades) == 1
        assert trades[0]["reason"].startswith("exit condition met")
        strategy_key = trades[0]["strategy_key"]
        assert database.strategy_stop_reason("BTC/USDT", strategy_key) is None
        run = database.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", ("run-monitor",)
        ).fetchone()
        assert run["status"] != "stopped"
    finally:
        database.close()


def test_edge_fade_expectancy_forces_flatten_and_stops_strategy() -> None:
    settings, database, market, execution = _setup()
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-edge-fade", "BTC/USDT", {})
        ExecutionNode(context)(_approved_state("run-edge-fade", market.candles))
        position = database.open_positions("BTC/USDT")[0]
        assert PositionMonitor(context).edge_fade_reason(position["strategy_key"]) is None
        for _ in range(settings.edge_fade_min_trades):
            database.record_closed_trade(
                run_id="run-edge-fade",
                symbol="BTC/USDT",
                strategy_key=position["strategy_key"],
                side="long",
                amount=0.1,
                entry_price=120,
                exit_price=115,
                net_pnl=-0.5,
                reason="negative prior result",
            )

        decision = PositionMonitor(context)()[0]
        assert decision["action"] == "edge_fade_exit"
        assert "edge faded" in decision["reason"]
        assert database.open_positions("BTC/USDT") == []
        assert database.strategy_stop_reason("BTC/USDT", position["strategy_key"])
        assert execution.exchange.order_count == 3
    finally:
        database.close()


def test_single_losing_trade_does_not_count_as_edge_fade() -> None:
    settings, database, _market, _execution = _setup()
    try:
        database.create_run("run-one-loss", "BTC/USDT", {})
        database.record_closed_trade(
            run_id="run-one-loss",
            symbol="BTC/USDT",
            strategy_key="key-1",
            side="long",
            amount=0.1,
            entry_price=120,
            exit_price=115,
            net_pnl=-0.5,
            reason="stop loss",
        )
        assert (
            database.recent_strategy_expectancy(
                "key-1", 10, min_trades=settings.edge_fade_min_trades
            )
            is None
        )
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


def test_entry_is_held_when_the_account_has_no_quote_balance() -> None:
    settings, database, market, execution = _setup()
    execution.exchange.free = {"USDT": 0.0}
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-broke", "BTC/USDT", {})

        result = ExecutionNode(context)(_approved_state("run-broke", market.candles))

        assert result["execution_result"].status == "hold"
        assert "Not enough USDT" in result["execution_result"].execution_result
        assert execution.exchange.order_count == 0
    finally:
        database.close()


def test_entry_is_shrunk_to_what_the_free_balance_can_buy() -> None:
    settings, database, market, execution = _setup()
    execution.exchange.free = {"USDT": 6.0, "BTC": 10.0}  # the plan wants ~12 USDT of BTC
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-small", "BTC/USDT", {})

        ExecutionNode(context)(_approved_state("run-small", market.candles))

        entry = execution.exchange.orders[0]
        assert entry["side"] == "buy"
        assert entry["amount"] * 120 * (1 + settings.fee_rate_taker) < 6.0
        events = [
            row["type"] for row in database.connection.execute("SELECT type FROM system_events")
        ]
        assert "order_limited_by_balance" in events
    finally:
        database.close()


def test_protective_stop_covers_only_the_coin_actually_received() -> None:
    settings, database, market, execution = _setup()
    execution.exchange.free = {"USDT": 100_000.0, "BTC": 0.0999}  # fee taken in BTC
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-fee", "BTC/USDT", {})

        ExecutionNode(context)(_approved_state("run-fee", market.candles))

        stop = next(order for order in execution.exchange.orders if "triggerPrice" in order["info"])
        assert stop["amount"] == 0.0999
        assert database.open_positions("BTC/USDT")[0]["amount"] == 0.0999
    finally:
        database.close()


def test_exit_sells_only_the_coin_held_and_closes_cleanly() -> None:
    settings, database, market, execution = _setup()
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-dust", "BTC/USDT", {})
        ExecutionNode(context)(_approved_state("run-dust", market.candles))
        execution.exchange.free = {"USDT": 100_000.0, "BTC": 0.0999}  # fee taken in BTC
        entry = market.candles[-1]
        market.candles = [*market.candles, _bar(entry.timestamp + timedelta(hours=1), 115, low=114)]

        decision = PositionMonitor(context)()[0]

        exit_order = execution.exchange.orders[-1]
        assert exit_order["side"] == "sell" and exit_order["amount"] == 0.0999
        assert decision["action"] == "position_closed"
        assert database.open_positions("BTC/USDT") == []
    finally:
        database.close()


def test_position_sold_outside_mats_is_recorded_without_an_order() -> None:
    settings, database, market, execution = _setup()
    try:
        context = _context(settings, database, market, execution)
        database.create_run("run-gone", "BTC/USDT", {})
        ExecutionNode(context)(_approved_state("run-gone", market.candles))
        execution.exchange.free = {"USDT": 100_000.0}  # the BTC was sold by hand
        orders_before = execution.exchange.order_count
        entry = market.candles[-1]
        market.candles = [*market.candles, _bar(entry.timestamp + timedelta(hours=1), 115, low=114)]

        decision = PositionMonitor(context)()[0]

        assert decision["action"] == "closed_outside_mats"
        assert execution.exchange.order_count == orders_before
        assert database.open_positions("BTC/USDT") == []
        assert database.strategy_trades("run-gone")[0]["reason"].startswith("No BTC is held")
    finally:
        database.close()
