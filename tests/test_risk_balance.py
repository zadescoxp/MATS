from types import SimpleNamespace

import pytest

import framework.agents.risk_assessment as risk_module
from framework.agents.risk_assessment import RiskAssessmentNode
from framework.config.settings import Settings
from framework.core.state import GraphState
from framework.schemas.models import AccountBalance, Critique, LiveSignal, StrategyDefinition


def _state(account_equity: float | None) -> GraphState:
    return GraphState(
        run_id="run",
        symbol="BTC/USDT",
        timeframes_to_consider=["1h"],
        account_equity=account_equity,
        strategy=StrategyDefinition(
            name="probe",
            description="probe",
            chosen_timeframe="1h",
            template_name="ema_crossover",
            entry_rule="ema_crossover",
            stop_loss_pct=0.02,
            position_fraction=1.0,
        ),
        critique=Critique(decision="APPROVE"),
    )


def _node(
    monkeypatch,
    free_usdt: float,
    *,
    live: bool = True,
    margin_on: bool = True,
    lendable=1e9,
    exposure: float = 1.0,
) -> RiskAssessmentNode:
    monkeypatch.setattr(risk_module, "fetch_strategy_ohlcv", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        risk_module,
        "evaluate_live_signal",
        lambda *_args, **_kwargs: LiveSignal(
            action="long", reason="probe", strength=1, price=100.0, stop=98.0
        ),
    )
    settings = Settings(dry_run=not live, kill_switch=False, max_account_exposure=exposure)
    info = {"result": {"list": [{"coin": [{"coin": "USDT", "availableToBorrow": lendable}]}]}}
    execution = SimpleNamespace(
        get_balance=lambda: AccountBalance(free={"USDT": free_usdt}, info=info),
        spot_margin_enabled=lambda: margin_on,
    )
    return RiskAssessmentNode(
        SimpleNamespace(settings=settings, execution=execution, market_data=None)
    )


def test_run_capital_above_the_free_balance_is_capped(monkeypatch) -> None:
    result = _node(monkeypatch, free_usdt=1000)(_state(account_equity=50_000))
    plan = result["risk_assessment"]

    assert plan.approved
    assert plan.position_notional <= 1000
    assert any("more than the 1,000.00 USDT available" in note for note in plan.account_constraints)


def test_no_free_quote_balance_sizes_to_zero_with_a_reason(monkeypatch) -> None:
    plan = _node(monkeypatch, free_usdt=0)(_state(account_equity=None))["risk_assessment"]

    assert not plan.approved
    assert plan.position_size == 0
    assert any("No USDT is available" in note for note in plan.account_constraints)


@pytest.mark.parametrize("free_usdt", [0, 10])
def test_dry_run_sizing_ignores_the_exchange_balance(monkeypatch, free_usdt) -> None:
    plan = _node(monkeypatch, free_usdt=free_usdt, live=False)(_state(account_equity=5000))[
        "risk_assessment"
    ]
    assert plan.approved
    assert plan.position_notional > 10


def _leveraged(account_equity: float | None, leverage: float) -> GraphState:
    return _state(account_equity).model_copy(update={"leverage": leverage})


def test_leverage_raises_exposure_but_not_the_loss_at_the_stop(monkeypatch) -> None:
    # A 25% exposure limit binds first; a 2% stop with a 1% loss budget allows 50% notional.
    unlevered = _node(monkeypatch, free_usdt=1000, exposure=0.25)(_leveraged(None, 1.0))[
        "risk_assessment"
    ]
    levered = _node(monkeypatch, free_usdt=1000, exposure=0.25)(_leveraged(None, 3.0))[
        "risk_assessment"
    ]

    assert unlevered.position_notional == 250
    assert levered.position_notional == 500  # lifted by leverage, then capped by the stop
    assert levered.risk_amount <= 1000 * 0.01 + 1e-9  # 1% of equity at the stop, still
    assert any("Leverage 3x" in note for note in levered.account_constraints)


def test_leverage_is_limited_by_what_bybit_will_lend(monkeypatch) -> None:
    plan = _node(monkeypatch, free_usdt=1000, lendable=200)(_leveraged(None, 3.0))[
        "risk_assessment"
    ]
    assert plan.position_notional <= 1200


def test_leverage_is_ignored_when_spot_margin_is_off(monkeypatch) -> None:
    plan = _node(monkeypatch, free_usdt=1000, margin_on=False)(_leveraged(None, 3.0))[
        "risk_assessment"
    ]
    assert plan.position_notional <= 1000
    assert any("Spot margin is off" in note for note in plan.account_constraints)
