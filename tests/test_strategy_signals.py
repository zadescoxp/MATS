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
        (
            "regime_gated_trend",
            [100] * 30 + [100, 98, 96, 94, 92, 90, 120],
            [100] * 30 + [100, 102, 104, 106, 108, 110, 80],
            {"fast_period": 3, "slow_period": 8, "adx_period": 3, "adx_minimum": 0},
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


def test_position_monitor_ignores_pre_entry_wick_in_entry_candle() -> None:
    definition = StrategyDefinition(
        name="EMA",
        description="EMA crossover",
        chosen_timeframe="1h",
        template_name="ema_crossover",
        entry_rule="ema_crossover",
        fast_period=3,
        slow_period=8,
    )
    bars = _bars(
        [99, 100],
        datetime(2026, 1, 1, tzinfo=UTC),
        timedelta(hours=1),
    )
    bars[-1] = bars[-1].model_copy(update={"low": 90})
    result = evaluate_live_signal(
        definition,
        {"1h": bars},
        {
            "side": "long",
            "entry_price": 100,
            "stop_price": 98,
            "opened_at": datetime(2026, 1, 1, 1, 30, tzinfo=UTC).isoformat(),
            "bars_held": 0,
        },
    )
    assert result.action == "hold"


def test_new_families_fire_on_their_own_setups() -> None:
    fade = _definition(
        "mean_reversion",
        {"z_period": 5, "z_entry": 0.5, "rsi_period": 2, "rsi_extreme": 5},
    )
    long_fade = evaluate_live_signal(fade, _data(_reversion_prices(True), "mean_reversion"))
    assert long_fade.action == "long"
    assert (
        evaluate_live_signal(fade, _data(_reversion_prices(False), "mean_reversion")).action
        == "short"
    )
    breakout = _definition(
        "breakout_confirmed",
        {"channel_period": 5, "atr_period": 3, "volume_multiple": 1.2, "atr_expansion": 0.5},
    )
    assert (
        evaluate_live_signal(breakout, _data(_breakout_prices(True), "breakout_confirmed")).action
        == "long"
    )
    assert (
        evaluate_live_signal(breakout, _data(_breakout_prices(False), "breakout_confirmed")).action
        == "short"
    )
    band = _definition("volatility_fade", {"bollinger_period": 5, "bollinger_deviations": 1.0})
    assert evaluate_live_signal(band, _data(_fade_prices(True), "volatility_fade")).action == "long"
    assert (
        evaluate_live_signal(
            _definition(
                "stat_arb_spread", {"z_period": 5, "z_entry": 0.5, "pair_symbol": "ETH/USDT"}
            ),
            _pair_data([100.0] * 12 + [100, 90, 80, 70, 60, 90]),
        ).action
        == "long"
    )
    assert (
        evaluate_live_signal(
            _definition(
                "stat_arb_spread", {"z_period": 5, "z_entry": 0.5, "pair_symbol": "ETH/USDT"}
            ),
            _pair_data([100.0] * 12 + [100, 110, 120, 130, 140, 110]),
        ).action
        == "short"
    )
    funding = _definition("carry_funding", {"funding_threshold": 0.0001})
    paid = _funding([0.001, -0.001])
    prices = _bars([100, 100], datetime(2026, 1, 1, tzinfo=UTC), timedelta(hours=1))
    assert evaluate_live_signal(funding, {"1h": prices, "funding": paid}).action == "long"


def _definition(template: str, parameters: dict) -> StrategyDefinition:
    return StrategyDefinition(
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


def _reversion_prices(long: bool) -> list[float]:
    base = [100.0] * 12
    if long:
        return base + [100, 90, 80, 70, 60, 90]
    return base + [100, 110, 120, 130, 140, 110]


def _breakout_prices(long: bool) -> list[float]:
    base = [100.0] * 12
    return base + ([140] if long else [60])


def _fade_prices(long: bool) -> list[float]:
    base = [100.0] * 12
    if long:
        return base + [70, 100]
    return base + [140, 100]


def _funding(rates: list[float]) -> list[OHLCVBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = []
    for index, rate in enumerate(rates):
        level = 1 + rate
        bars.append(
            OHLCVBar(
                timestamp=start + timedelta(hours=index),
                open=level,
                high=level,
                low=level,
                close=level,
                volume=0,
            )
        )
    return bars


def _pair_data(prices: list[float]) -> dict[str, list[OHLCVBar]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "1h": _bars(prices, start, timedelta(hours=1)),
        "pair": _bars([100.0] * len(prices), start, timedelta(hours=1)),
    }


def _data(prices: list[float], template: str) -> dict[str, list[OHLCVBar]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    lower = _bars(prices, start, timedelta(hours=1))
    result = {"1h": lower}
    if template == "stat_arb_spread":
        pair_prices = [100.0] * (len(prices) - 1) + [70.0]
        result["pair"] = _bars(pair_prices, start, timedelta(hours=1))
    if template == "breakout_confirmed":
        result["1h"] = _volume_spike(lower)
    if template in {"macd_htf_bias", "htf_trend_ltf_timing", "multi_timeframe_hybrid"}:
        trending = [100 + index for index in range(80)]
        if prices[-1] < prices[0]:
            trending = [180 - index for index in range(80)]
        htf_end = lower[-1].timestamp
        htf_start = htf_end - 79 * timedelta(hours=4)
        result["4h"] = _bars(trending, htf_start, timedelta(hours=4))
    return result


def _volume_spike(bars: list[OHLCVBar]) -> list[OHLCVBar]:
    spiked = list(bars)
    spiked[-1] = spiked[-1].model_copy(update={"volume": 500})
    return spiked


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
