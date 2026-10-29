from datetime import UTC, datetime, timedelta

import pytest

from framework.schemas.models import OHLCVBar, StrategyDefinition
from framework.strategies.library import STRATEGY_LIBRARY
from framework.strategies.signals import evaluate_live_signal


@pytest.mark.parametrize(
    ("template", "long_prices", "short_prices", "parameters"),
    [
        (
            "ema_crossover",
            [100] * 30 + [100, 98, 96, 94, 92, 90, 120],
            [100] * 30 + [100, 102, 104, 106, 108, 110, 80],
            {"fast_period": 3, "slow_period": 8},
        ),
        (
            "rsi_mean_reversion",
            list(range(100, 140)) + [139, 138, 139],
            list(range(100, 140)) + [141, 142, 141],
            {"rsi_period": 2, "oversold": 40, "overbought": 60, "trend_period": 2},
        ),
        (
            "supertrend_atr",
            list(range(120, 90, -1)) + [130],
            list(range(90, 120)) + [70],
            {
                "atr_period": 3,
                "supertrend_multiplier": 1.0,
                "minimum_atr_pct": 0.0,
                "maximum_atr_pct": 0.5,
            },
        ),
        (
            "macd_htf_bias",
            list(range(130, 100, -1)) + [140],
            list(range(100, 130)) + [80],
            {
                "macd_fast": 3,
                "macd_slow": 5,
                "macd_signal": 2,
                "higher_timeframe": "4h",
                "bias_fast_period": 3,
                "bias_slow_period": 8,
            },
        ),
        (
            "bollinger_squeeze",
            [100] * 30 + [120],
            [100] * 30 + [80],
            {
                "bollinger_period": 5,
                "bollinger_deviations": 1.0,
                "squeeze_width_pct": 0.01,
            },
        ),
        (
            "htf_trend_ltf_timing",
            [100] * 30 + [100, 98, 96, 94, 92, 90, 120],
            [100] * 30 + [100, 102, 104, 106, 108, 110, 80],
            {
                "fast_period": 3,
                "slow_period": 8,
                "higher_timeframe": "4h",
                "bias_fast_period": 3,
                "bias_slow_period": 8,
            },
        ),
    ],
)
def test_library_templates_evaluate_long_short_and_hold(
    template: str,
    long_prices: list[float],
    short_prices: list[float],
    parameters: dict,
) -> None:
    assert template in STRATEGY_LIBRARY
    definition = StrategyDefinition(
        name=template,
        description=STRATEGY_LIBRARY[template]["description"],
        chosen_timeframe="1h",
        template_name=template,
        entry_rule=template,
        parameters=parameters,
        fast_period=int(parameters.get("fast_period", parameters.get("macd_fast", 12))),
        slow_period=int(parameters.get("slow_period", parameters.get("macd_slow", 26))),
        stop_loss_pct=0.02,
    )
    assert evaluate_live_signal(definition, _data(long_prices, template)).action == "long"
    assert evaluate_live_signal(definition, _data(short_prices, template)).action == "short"
    assert evaluate_live_signal(definition, _data([100] * 60, template)).action == "hold"


def test_position_exit_reports_strategy_over_on_stop_hit() -> None:
    definition = StrategyDefinition(
        name="EMA",
        description="EMA crossover",
        chosen_timeframe="1h",
        template_name="ema_crossover",
        entry_rule="ema_crossover",
        fast_period=3,
        slow_period=8,
    )
    data = _data([100, 101, 102, 103, 104, 95], "ema_crossover")
    result = evaluate_live_signal(
        definition,
        data,
        {"side": "long", "entry_price": 100, "stop_price": 98, "bars_held": 1},
    )
    assert result.action == "exit"
    assert result.strategy_over
    assert result.reason == "Protective stop was hit."


def _data(prices: list[float], template: str) -> dict[str, list[OHLCVBar]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    lower = _bars(prices, start, timedelta(hours=1))
    result = {"1h": lower}
    if template in {"macd_htf_bias", "htf_trend_ltf_timing"}:
        trending = [100 + index for index in range(80)]
        if prices[-1] < prices[0]:
            trending = [180 - index for index in range(80)]
        htf_end = lower[-1].timestamp
        htf_start = htf_end - 79 * timedelta(hours=4)
        result["4h"] = _bars(trending, htf_start, timedelta(hours=4))
    return result


def _bars(prices: list[float], start: datetime, interval: timedelta) -> list[OHLCVBar]:
    result = []
    for index, price in enumerate(prices):
        prior = prices[index - 1] if index else price
        result.append(
            OHLCVBar(
                timestamp=start + index * interval,
                open=prior,
                high=max(prior, price) + 0.2,
                low=min(prior, price) - 0.2,
                close=price,
                volume=10,
            )
        )
    return result
