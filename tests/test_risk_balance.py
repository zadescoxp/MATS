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


def _node(monkeypatch, free_usdt: float, *, live: bool = True) -> RiskAssessmentNode:
    monkeypatch.setattr(risk_module, "fetch_strategy_ohlcv", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        risk_module,
        "evaluate_live_signal",
        lambda *_args, **_kwargs: LiveSignal(
            action="long", reason="probe", strength=1, price=100.0, stop=98.0
        ),
    )
    settings = Settings(dry_run=not live, kill_switch=False, max_account_exposure=1.0)
    execution = SimpleNamespace(get_balance=lambda: AccountBalance(free={"USDT": free_usdt}))
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
