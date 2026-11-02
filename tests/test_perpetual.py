from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

import framework.agents.risk_assessment as risk_module
from framework.agents.execution import ExecutionNode
from framework.agents.position_monitor import PositionMonitor
from framework.agents.risk_assessment import RiskAssessmentNode
from framework.agents.strategy_builder import _template_strategy, strategy_series
from framework.config.settings import Settings
from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.persistence.database import Database
from framework.schemas.models import (
    AccountBalance,
    Critique,
    LiveSignal,
    OHLCVBar,
    RiskAssessment,
    StrategyDefinition,
)
from framework.strategies.library import strategy_fingerprint
from framework.tools.backtest import BacktestTool
from framework.tools.context_feed import reference_pair
from framework.tools.cost_model import CostAssumptions
from framework.tools.execution import ExecutionTools
from framework.tools.markets import (
    MarketListing,
    is_perpetual,
    quote_currency,
    same_market,
    tradable_markets,
)
from framework.web import _symbols

PERP = "BTC/USDT:USDT"
NO_COSTS = CostAssumptions(maker_fee_rate=0, taker_fee_rate=0, slippage_percent=0)
START = datetime(2026, 1, 1, tzinfo=UTC)


def _bar(index: int, close: float, *, high: float | None = None, low: float | None = None):
    return OHLCVBar(
        timestamp=START + timedelta(hours=index),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=10,
    )


def _strategy(**update) -> StrategyDefinition:
    fields = {
        "name": "probe",
        "description": "probe",
        "chosen_timeframe": "1h",
        "template_name": "ema_crossover",
        "entry_rule": "ema_crossover",
        "stop_loss_pct": 0.1,
        "position_fraction": 0.5,
    }
    return StrategyDefinition(**{**fields, **update})


def _signals(actions: list[str], prices: list[float]) -> list[LiveSignal]:
    return [
        LiveSignal(action=action, reason="scripted", strength=0, price=price)
        for action, price in zip(actions, prices, strict=True)
    ]


# --- Market identity ----------------------------------------------------------------


def test_symbols_name_their_market() -> None:
    assert is_perpetual(PERP) and not is_perpetual("BTC/USDT")
    assert quote_currency(PERP) == "USDT" and quote_currency("ETH/USDC") == "USDC"
    assert same_market("ETH/USDT", PERP) == "ETH/USDT:USDT"
    assert same_market("ETH/USDT:USDT", "BTC/USDT") == "ETH/USDT"


def test_reference_pairs_stay_on_the_same_market_and_cover_every_coin() -> None:
    assert reference_pair("BTC/USDT") == "ETH/USDT"
    assert reference_pair(PERP) == "ETH/USDT:USDT"
    assert reference_pair("PEPE/USDT") == "BTC/USDT"
    assert reference_pair("PEPE/USDC:USDC") == "BTC/USDC:USDC"


def test_listing_keeps_spot_and_linear_perpetuals_only() -> None:
    class Exchange:
        def load_markets(self):
            def market(symbol, **flags):
                base, rest = symbol.split("/")
                quote = rest.split(":")[0]
                settle = rest.split(":")[1] if ":" in rest else None
                return {"symbol": symbol, "base": base, "quote": quote, "settle": settle, **flags}

            rows = [
                market("BTC/USDT", spot=True),
                market("BTC/USDT:USDT", swap=True, linear=True),
                market("BTC/USD:BTC", swap=True, linear=False),  # inverse
                market("BTC/USDT:USDT-260327", future=True, linear=True),  # dated future
                market("OLD/USDT", spot=True, active=False),
                market("BTC/EUR", spot=True),
            ]
            return {row["symbol"]: row for row in rows}

    markets = tradable_markets(Exchange())

    assert [(row["symbol"], row["type"]) for row in markets] == [
        ("BTC/USDT:USDT", "perpetual"),
        ("BTC/USDT", "spot"),
    ]
    listing = MarketListing(Exchange)
    assert listing.known(PERP) is None  # nothing loaded yet, so nothing is refused
    assert listing.contains(PERP) and listing.known("BTC/USD:BTC") is False


def test_console_accepts_perpetual_symbols() -> None:
    assert _symbols(["BTC/USDT", PERP, "1000PEPE/USDT:USDT"], []) == [
        "BTC/USDT",
        PERP,
        "1000PEPE/USDT:USDT",
    ]
    with pytest.raises(ValueError):
        _symbols(["BTC/USDT:USDC"], [])  # linear perpetuals settle in their quote coin


def test_only_perpetual_strategies_can_short() -> None:
    with pytest.raises(ValidationError):
        _strategy(short_entries=True)
    spot = _strategy()
    perpetual = _strategy(market="perpetual", short_entries=True)
    assert strategy_fingerprint(spot) != strategy_fingerprint(perpetual)
    # Strategies saved before markets existed keep their fingerprint.
    assert '"market"' not in strategy_fingerprint(spot)


# --- Backtest -----------------------------------------------------------------------


def test_short_profits_from_a_falling_price() -> None:
    prices = [100, 100, 90, 80, 80]
    candles = [_bar(index, price) for index, price in enumerate(prices)]
    signals = _signals(["short", "hold", "hold", "long", "hold"], prices)
    report = BacktestTool(NO_COSTS).simulate(
        _strategy(market="perpetual", short_entries=True), candles, signals, 10_000
    )

    short = report.trades[0]
    assert (short.side, short.entry_price, short.exit_price) == ("short", 100, 80)
    assert short.exit_reason == "opposite_signal"
    assert short.net_pnl == pytest.approx(0.2 * 5_000)
    # Marked to market while open: 10% down is 500 of profit on a 5,000 notional.
    assert report.equity_curve[2]["equity"] == pytest.approx(10_500)
    # The long signal that closed the short opened a long on the same bar.
    assert report.trades[1].side == "long"
    assert report.final_equity == pytest.approx(11_000)


def test_spot_strategies_ignore_short_signals() -> None:
    prices = [100, 100, 90, 80]
    candles = [_bar(index, price) for index, price in enumerate(prices)]
    signals = _signals(["short", "hold", "hold", "hold"], prices)
    for strategy in (_strategy(), _strategy(market="perpetual")):
        report = BacktestTool(NO_COSTS).simulate(strategy, candles, signals, 10_000)
        assert report.total_trades == 0


def test_short_stop_triggers_on_a_rally() -> None:
    prices = [100, 100, 105, 120]
    candles = [_bar(0, 100), _bar(1, 100), _bar(2, 105, high=112), _bar(3, 120)]
    signals = _signals(["short", "hold", "hold", "hold"], prices)
    report = BacktestTool(NO_COSTS).simulate(
        _strategy(market="perpetual", short_entries=True), candles, signals, 10_000
    )

    trade = report.trades[0]
    assert trade.exit_reason == "stop_loss" and trade.exit_price == pytest.approx(110)
    assert trade.net_pnl == pytest.approx(-0.1 * 5_000)


def test_perpetual_positions_pay_funding_while_open() -> None:
    prices = [100.0] * 6
    candles = [_bar(index, price) for index, price in enumerate(prices)]
    # 0.1% funding settles at hour 2 (held) and hour 5 (after the exit).
    funding = [_bar(2, 1.001), _bar(5, 1.001)]
    signals = {
        # Entered at hour 1, closed by the 3-bar time stop at hour 4.
        "long": _signals(["long", "hold", "hold", "hold", "hold", "hold"], prices),
        "short": _signals(["short", "hold", "hold", "hold", "hold", "hold"], prices),
    }
    tool = BacktestTool(NO_COSTS)
    strategy = _strategy(market="perpetual", short_entries=True, time_stop_bars=3)

    long = tool.simulate(strategy, candles, signals["long"], 10_000, funding=funding)
    short = tool.simulate(strategy, candles, signals["short"], 10_000, funding=funding)
    spot = tool.simulate(
        _strategy(time_stop_bars=3), candles, signals["long"], 10_000, funding=funding
    )

    assert long.trades[0].funding == pytest.approx(5.0)  # 5,000 notional x 0.1%
    assert long.trades[0].net_pnl == pytest.approx(-5.0)
    assert long.funding_cost == pytest.approx(5.0)
    assert short.trades[0].funding == pytest.approx(-5.0)  # shorts receive it
    assert short.trades[0].net_pnl == pytest.approx(5.0)
    assert spot.funding_cost == 0 and spot.trades[0].net_pnl == 0


def test_perpetual_strategies_pay_perpetual_fees() -> None:
    prices = [100.0, 100.0, 100.0]
    candles = [_bar(index, price) for index, price in enumerate(prices)]
    signals = _signals(["long", "hold", "hold"], prices)
    tool = BacktestTool(
        CostAssumptions(taker_fee_rate=0.001, slippage_percent=0),
        perpetual_costs=CostAssumptions(taker_fee_rate=0.0005, slippage_percent=0),
    )

    spot = tool.simulate(_strategy(), candles, signals, 10_000)
    perpetual = tool.simulate(_strategy(market="perpetual"), candles, signals, 10_000)

    assert perpetual.total_fees == pytest.approx(spot.total_fees / 2, rel=1e-3)


# --- Builder ------------------------------------------------------------------------


def test_builder_makes_short_capable_strategies_with_funding_for_perpetuals() -> None:
    strategy = _template_strategy(
        "supertrend_atr", "1h", {"atr_period": 10}, market="perpetual", short_entries=True
    )
    assert strategy.market == "perpetual" and strategy.short_entries

    funding = [_bar(0, 1.0001)]
    for symbol, expected in ((PERP, True), ("BTC/USDT", False)):
        state = GraphState(
            run_id="run",
            symbol=symbol,
            timeframes_to_consider=["1h"],
            raw_data={"1h": [_bar(0, 100)]},
            market_context={"funding_history": funding},
        )
        series = strategy_series(state, "1h", "supertrend_atr", {})
        assert ("funding" in series) is expected


# --- Risk ---------------------------------------------------------------------------


def _risk_node(
    monkeypatch, *, live: bool, free: float = 1_000, stop: float = 102.0
) -> RiskAssessmentNode:
    monkeypatch.setattr(risk_module, "fetch_strategy_ohlcv", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        risk_module,
        "evaluate_live_signal",
        lambda *_args, **_kwargs: LiveSignal(
            action="short", reason="probe", strength=1, price=100.0, stop=stop
        ),
    )
    settings = Settings(dry_run=not live, kill_switch=False, max_account_exposure=1.0)
    execution = SimpleNamespace(
        get_balance=lambda: AccountBalance(free={"USDT": free}),
        spot_margin_enabled=lambda: pytest.fail("perpetuals never check spot margin"),
    )
    return RiskAssessmentNode(
        SimpleNamespace(settings=settings, execution=execution, market_data=None)
    )


def _risk_state(symbol: str, strategy: StrategyDefinition, leverage: float | None = None):
    return GraphState(
        run_id="run",
        symbol=symbol,
        timeframes_to_consider=["1h"],
        account_equity=1_000,
        leverage=leverage,
        strategy=strategy.model_copy(update={"position_fraction": 1.0}),
        critique=Critique(decision="APPROVE"),
    )


def test_perpetual_shorts_are_sized_and_spot_shorts_are_not(monkeypatch) -> None:
    perpetual = _strategy(market="perpetual", short_entries=True)
    plan = _risk_node(monkeypatch, live=True)(_risk_state(PERP, perpetual))["risk_assessment"]
    assert plan.approved and plan.side == "short" and plan.stop_price == 102
    # 1% of 1,000 at risk over a 2-point stop: 5 contracts, under the 1,000 margin.
    assert plan.position_size == pytest.approx(5)

    spot = _risk_node(monkeypatch, live=False)(_risk_state("BTC/USDT", _strategy()))
    assert not spot["risk_assessment"].approved


def test_perpetual_leverage_lifts_the_margin_cap_without_borrowing(monkeypatch) -> None:
    strategy = _strategy(market="perpetual", short_entries=True)
    # A tight stop lets the 1% loss budget allow 2 contracts (200 notional).
    node = _risk_node(monkeypatch, live=True, free=100, stop=100.5)
    one_x = node(_risk_state(PERP, strategy))["risk_assessment"]
    three_x = node(_risk_state(PERP, strategy, leverage=3))["risk_assessment"]

    assert one_x.position_notional < 100
    assert 100 < three_x.position_notional < 300
    assert any("contract leverage" in note for note in three_x.account_constraints)


# --- Live execution and exits -------------------------------------------------------


class PerpetualExchange:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.contracts = 0.0
        self.repays: list[dict] = []

    def fetch_balance(self):
        free = {"USDT": 100_000.0}
        return {"free": free, "used": {}, "total": free, "info": {}}

    def fetch_positions(self, symbols):
        return [{"symbol": symbols[0], "contracts": self.contracts}]

    def request(self, path, api, method, params):
        self.repays.append(params)
        return {"retCode": 0, "result": {"resultStatus": "SU"}}

    def create_order(self, symbol, order_type, side, amount, price, params):
        trigger = "triggerPrice" in params
        order = {
            "id": f"exchange-{len(self.orders) + 1}",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "status": "open" if trigger else "closed",
            "amount": amount,
            "filled": 0 if trigger else amount,
            "remaining": 0,
            "average": 80,
            "price": price,
            "info": dict(params),
        }
        if not trigger:
            self.contracts += amount if side == "sell" else -amount
        self.orders.append(order)
        return order

    def cancel_order(self, order_id, symbol):
        return {"id": order_id, "symbol": symbol, "status": "canceled"}

    def fetch_order(self, order_id, symbol, params=None):
        return next(order for order in self.orders if order["id"] == order_id)


class Market:
    def __init__(self, candles: list[OHLCVBar]) -> None:
        self.candles = candles

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int):
        return SimpleNamespace(candles=self.candles[-limit:])


def _live_setup():
    settings = Settings(
        database_path=Path(":memory:"),
        bybit_api_key=SecretStr("test-key"),
        bybit_api_secret=SecretStr("test-secret"),
        bybit_testnet=True,
        dry_run=False,
        kill_switch=False,
    )
    database = Database(":memory:")
    # Flat, a rally, then a drop: the fast EMA crosses below the slow one on the last bar.
    prices = [100] * 30 + [100, 102, 104, 106, 108, 110, 80]
    market = Market([_bar(index, price) for index, price in enumerate(prices)])
    exchange = PerpetualExchange()
    execution = ExecutionTools(settings, exchange=exchange)
    context = AgentContext(
        settings=settings,
        database=database,
        llm=None,
        market_data=market,
        backtester=None,
        execution=execution,
    )
    return context, database, market, exchange


def _short_state(run_id: str, candles: list[OHLCVBar], symbol: str = PERP) -> GraphState:
    perpetual = is_perpetual(symbol)
    return GraphState(
        run_id=run_id,
        symbol=symbol,
        timeframes_to_consider=["1h"],
        raw_data={"1h": candles},
        strategy=_strategy(
            fast_period=3,
            slow_period=8,
            stop_loss_pct=0.02,
            market="perpetual" if perpetual else "spot",
            short_entries=perpetual,
        ),
        critique=Critique(decision="APPROVE"),
        risk_assessment=RiskAssessment(
            approved=True,
            side="short",
            position_size=0.1,
            position_notional=8,
            risk_amount=0.16,
            max_concurrent_exposure=0.001,
            stop_price=81.6,
        ),
    )


def test_perpetual_short_enters_with_a_reduce_only_stop_and_exits_reduce_only() -> None:
    context, database, market, exchange = _live_setup()
    try:
        database.create_run("run-short", PERP, {})
        report = ExecutionNode(context)(_short_state("run-short", market.candles))[
            "execution_result"
        ]
        assert report.status == "filled"

        entry, stop = exchange.orders
        assert entry["side"] == "sell" and entry["amount"] == pytest.approx(0.1)
        assert "marketUnit" not in entry["info"] and "isLeverage" not in entry["info"]
        assert "reduceOnly" not in entry["info"]  # the entry opens the short
        assert stop["side"] == "buy" and stop["info"]["triggerDirection"] == "ascending"
        assert stop["info"]["reduceOnly"] is True
        position = database.open_positions(PERP)[0]
        assert position["side"] == "short" and position["take_profit_price"] is None

        # The price rallies through the stop on the next candle.
        market.candles = [*market.candles, _bar(len(market.candles), 90, high=91)]
        decision = PositionMonitor(context)()[0]

        exit_order = exchange.orders[-1]
        assert decision["action"] == "position_closed"
        assert exit_order["side"] == "buy" and exit_order["info"]["reduceOnly"] is True
        assert exit_order["amount"] == pytest.approx(0.1)
        assert exchange.contracts == pytest.approx(0)
        assert database.open_positions(PERP) == []
        assert database.strategy_trades("run-short")[0]["side"] == "short"
        assert exchange.repays == []
    finally:
        database.close()


def test_perpetual_position_closed_on_bybit_is_recorded_without_an_order() -> None:
    context, database, market, exchange = _live_setup()
    try:
        database.create_run("run-gone", PERP, {})
        ExecutionNode(context)(_short_state("run-gone", market.candles))
        exchange.contracts = 0.0  # closed by hand on Bybit
        orders_before = len(exchange.orders)
        market.candles = [*market.candles, _bar(len(market.candles), 90, high=91)]

        decision = PositionMonitor(context)()[0]

        assert decision["action"] == "closed_outside_mats"
        assert len(exchange.orders) == orders_before
        assert database.open_positions(PERP) == []
    finally:
        database.close()


def test_spot_short_signal_places_no_order() -> None:
    context, database, market, exchange = _live_setup()
    try:
        database.create_run("run-spot", "BTC/USDT", {})
        result = ExecutionNode(context)(_short_state("run-spot", market.candles, "BTC/USDT"))

        assert result["execution_result"].status == "hold"
        assert "does not open shorts" in result["execution_result"].execution_result
        assert exchange.orders == []
    finally:
        database.close()


def test_perpetual_research_fetches_funding_for_the_whole_history() -> None:
    from framework.agents.data_worker import DataWorkerNode
    from framework.tools.market_data import TickerSnapshot

    calls: list[tuple[str, dict]] = []
    candles = [_bar(index, 100) for index in range(3)]
    market_data = SimpleNamespace(
        fetch_ohlcv=lambda symbol, timeframe, limit: SimpleNamespace(candles=candles),
        fetch_realtime_ticker=lambda symbol: TickerSnapshot(symbol=symbol, last=100),
        fetch_funding_history=lambda symbol, **kwargs: calls.append((symbol, kwargs))
        or [_bar(1, 1.0001)],
    )
    settings = Settings(include_external_context=False, include_pair_context=False)
    node = DataWorkerNode(SimpleNamespace(settings=settings, market_data=market_data))

    for symbol in (PERP, "BTC/USDT"):
        state = GraphState(run_id="run", symbol=symbol, timeframes_to_consider=["1h"])
        assert node(state)["market_context"]["funding_history"]

    # Spot carry reads the perpetual's funding rate; spot itself has none.
    assert calls == [(PERP, {"since": int(START.timestamp() * 1000)}), (PERP, {})]


def test_console_lists_markets_and_refuses_unknown_ones(tmp_path, monkeypatch) -> None:
    from framework.web import DashboardService

    service = DashboardService(Settings(database_path=tmp_path / "state.db"))
    listed = [
        {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "type": "spot"},
        {"symbol": PERP, "base": "BTC", "quote": "USDT", "type": "perpetual"},
    ]
    monkeypatch.setattr("framework.tools.markets.tradable_markets", lambda exchange: listed)
    service._market_listing._exchange_factory = lambda: None

    assert service.markets() == {"markets": listed}
    with pytest.raises(ValueError, match="Not a Bybit spot or perpetual market: DOGE/USDT"):
        service.start({"symbols": ["DOGE/USDT"]})
