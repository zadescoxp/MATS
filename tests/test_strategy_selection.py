from framework.agents.strategy_builder import _resolve_measured
from framework.schemas.models import StrategyDefinition


def _strategy(template: str, timeframe: str, **overrides) -> StrategyDefinition:
    values = {
        "name": f"{timeframe} {template}",
        "description": "measured",
        "chosen_timeframe": timeframe,
        "template_name": template,
        "entry_rule": template,
        "parameters": {"higher_timeframe": "4h"},
        "stop_loss_pct": 0.02,
    }
    values.update(overrides)
    return StrategyDefinition(**values)


def _row(strategy: StrategyDefinition, pnl: float) -> dict:
    return {"strategy": strategy, "net_pnl": pnl}


def test_parameter_drift_keeps_the_measured_row():
    measured = _strategy(
        "mean_reversion",
        "1h",
        parameters={"z_period": 20, "z_entry": 2.0, "higher_timeframe": "4h"},
    )
    drifted = measured.model_copy(
        update={
            "description": "Fade the stretch because the regime is choppy.",
            "parameters": {"z_period": 20, "z_entry": 1.5},
            "fast_period": 8,
            "slow_period": 21,
            "stop_loss_pct": 0.03,
        }
    )
    other = _strategy("supertrend_atr", "1h")
    chosen, match = _resolve_measured(drifted, [_row(measured, 12.0), _row(other, 40.0)])

    assert match == "template_timeframe"
    assert chosen.parameters == measured.parameters
    assert chosen.stop_loss_pct == measured.stop_loss_pct
    assert chosen.fast_period == measured.fast_period


def test_unknown_template_uses_the_same_family_when_one_was_measured():
    measured = _strategy("supertrend_atr", "1h")
    submitted = _strategy("ema_crossover", "15m", parameters={"fast_period": 9})
    chosen, match = _resolve_measured(
        submitted, [_row(measured, 8.0), _row(_strategy("mean_reversion", "1h"), 30.0)]
    )

    assert match == "family"
    assert chosen.template_name == "supertrend_atr"


def test_exact_copy_is_reported_as_exact():
    measured = _strategy("bollinger_squeeze", "1h")
    chosen, match = _resolve_measured(measured, [_row(measured, 4.0)])

    assert match == "exact"
    assert chosen is measured


def test_choice_by_candidate_id_uses_that_measured_row():
    from framework.agents.strategy_builder import _resolve_choice
    from framework.schemas.models import StrategyChoice

    first = _strategy("mean_reversion", "1h")
    second = _strategy("supertrend_atr", "15m")
    rows = [
        {**_row(first, 30.0), "candidate_id": "c1"},
        {**_row(second, 5.0), "candidate_id": "c2"},
    ]

    chosen, match = _resolve_choice(StrategyChoice(candidate_id="c2"), rows)

    assert chosen is second
    assert match == "exact"


def test_choice_by_template_and_timeframe_without_name_or_description():
    from framework.agents.strategy_builder import _resolve_choice
    from framework.schemas.models import StrategyChoice

    hourly = _strategy("mean_reversion", "1h")
    rows = [{**_row(hourly, 30.0), "candidate_id": "c1"}]
    choice = StrategyChoice.model_validate(
        {"template_name": "mean_reversion", "chosen_timeframe": "1h"}
    )

    chosen, match = _resolve_choice(choice, rows)

    assert chosen is hourly
    assert match == "exact"


def test_choice_must_name_a_candidate():
    import pytest
    from pydantic import ValidationError

    from framework.schemas.models import StrategyChoice

    with pytest.raises(ValidationError):
        StrategyChoice.model_validate({"description": "anything"})


def test_no_candidate_detail_names_the_real_reason_per_timeframe():
    from framework.agents.strategy_builder import _no_candidate_detail

    coverage = {
        "5m": {
            "bars": 500,
            "skipped_templates": {
                "timeframe_not_supported": ["a", "b"],
                "needs_higher_timeframe": ["macd_htf_bias"],
            },
            "too_few_trades": [{"template_name": "supertrend_atr", "closed_trades": 0}],
        },
        "1m": {"bars": 500, "skipped_templates": {"timeframe_not_supported": ["a"] * 13}},
        "1h": {"bars": 40, "reason": "insufficient_history"},
    }

    detail = _no_candidate_detail(coverage, min_bars=100, min_trades=3)

    assert detail.startswith("1m: no strategy template trades this timeframe.")
    assert "5m: 2 templates do not trade 5m; 1 need a timeframe above 5m in the run" in detail
    assert "too few closed trades (supertrend_atr 0; need 3)" in detail
    assert "1h: only 40 bars (need 100)." in detail
