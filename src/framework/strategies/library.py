import json
from typing import Any

from framework.schemas.models import StrategyDefinition

_EXIT = ["opposite_signal", "stop_loss", "take_profit", "time_stop"]
_RISK = {"stop_loss_pct": 0.02, "take_profit_pct": 0.04}
_INTRADAY = ["5m", "15m", "1h", "4h", "1d"]


def _template(**fields: Any) -> dict[str, Any]:
    fields.setdefault("exit_conditions", list(_EXIT))
    fields.setdefault("default_exit_rule", "opposite_signal")
    fields.setdefault("default_risk", dict(_RISK))
    fields.setdefault("pure_momentum", False)
    fields.setdefault("cost_sensitivity", "medium")
    fields.setdefault("typical_holding_period", "hours to a few days")
    return fields


STRATEGY_LIBRARY: dict[str, dict[str, Any]] = {
    "ema_crossover": _template(
        name="EMA crossover",
        family="momentum",
        thesis="Trend persists after a fast average overtakes a slow average.",
        entry="Fast EMA crosses the slow EMA.",
        exit="Opposite cross, or a stop.",
        regime_filter="None. This is a pure moving-average cross.",
        cost_sensitivity="high",
        typical_holding_period="several bars to a few days",
        suitable_regimes=["trending_high_vol", "trending_low_vol", "trending_normal_vol"],
        pure_momentum=True,
        description="Trade fast/slow EMA crossovers with ATR-aware risk controls.",
        required_indicators=["EMA"],
        entry_conditions={
            "long": "fast EMA crosses above slow EMA",
            "short": "fast EMA crosses below slow EMA",
        },
        default_exit_rule="opposite_signal",
        default_parameters={"fast_period": 12, "slow_period": 26},
        default_risk={"stop_loss_pct": 0.02, "take_profit_pct": None},
        supported_timeframes=list(_INTRADAY),
    ),
    "rsi_mean_reversion": _template(
        name="RSI mean-reversion / trend pullback",
        family="mean_reversion",
        thesis="A short RSI extreme inside a higher-timeframe trend is a pullback.",
        entry="RSI recovers through an extreme while price holds the trend EMA.",
        exit="Fixed reward multiple, opposite signal, or stop.",
        regime_filter="Trade only on the side of the trend EMA.",
        cost_sensitivity="medium",
        typical_holding_period="a few bars to a couple of days",
        suitable_regimes=["choppy_low_vol", "choppy_normal_vol", "trending_low_vol"],
        description="Enter on RSI recovery from an extreme in the EMA trend direction.",
        required_indicators=["RSI", "EMA"],
        entry_conditions={
            "long": "close above trend EMA and RSI crosses above oversold threshold",
            "short": "close below trend EMA and RSI crosses below overbought threshold",
        },
        default_exit_rule="fixed_risk_reward",
        default_parameters={
            "rsi_period": 14,
            "oversold": 30,
            "overbought": 70,
            "trend_period": 50,
        },
        supported_timeframes=["15m", "1h", "4h", "1d"],
    ),
    "mean_reversion": _template(
        name="Mean reversion",
        family="mean_reversion",
        thesis=(
            "In a range, price stretched from its recent mean tends to snap back. "
            "The edge is the reversion, not a new trend."
        ),
        entry=(
            "RSI(2) leaves an extreme, or close z-score crosses back through the band, "
            "while the bar closes back inside the recent range."
        ),
        exit="Return toward the mean, fixed reward, or a tight stop beyond the extreme.",
        regime_filter="Only when trend strength is low. Skip when the market is trending.",
        cost_sensitivity="high",
        typical_holding_period="one to several bars",
        suitable_regimes=["choppy_low_vol", "choppy_normal_vol", "choppy_high_vol"],
        description="Fade z-score and short-RSI extremes back toward the mean.",
        required_indicators=["RSI", "SMA", "Bollinger Bands"],
        entry_conditions={
            "long": "z-score or RSI(2) crosses up from an extreme and close reclaims",
            "short": "z-score or RSI(2) crosses down from an extreme and close loses",
        },
        default_exit_rule="fixed_risk_reward",
        default_parameters={
            "z_period": 20,
            "z_entry": 1.5,
            "rsi_period": 2,
            "rsi_extreme": 10,
        },
        default_risk={"stop_loss_pct": 0.015, "take_profit_pct": 0.02},
        supported_timeframes=["15m", "1h", "4h", "1d"],
    ),
    "stat_arb_spread": _template(
        name="Statistical arbitrage / relative value",
        family="relative_value",
        thesis=(
            "A cointegrated pair's ratio is noisy around a stable relationship. "
            "Trade the dislocation, not the outright direction."
        ),
        entry="Ratio z-score crosses back from an extreme versus the reference pair.",
        exit="Ratio returns toward its mean, or a stop on a broken relationship.",
        regime_filter="Any regime, but only while the pair series is available.",
        cost_sensitivity="high",
        typical_holding_period="hours to a few days",
        suitable_regimes=[
            "choppy_low_vol",
            "choppy_normal_vol",
            "choppy_high_vol",
            "trending_low_vol",
            "trending_normal_vol",
            "trending_high_vol",
        ],
        description="Trade a BTC/ETH-style ratio z-score back toward its mean.",
        required_indicators=["z-score"],
        entry_conditions={
            "long": "symbol/pair ratio z-score crosses up from below the short threshold",
            "short": "symbol/pair ratio z-score crosses down from above the long threshold",
        },
        default_exit_rule="fixed_risk_reward",
        default_parameters={"z_period": 30, "z_entry": 1.5, "pair_symbol": "ETH/USDT"},
        default_risk={"stop_loss_pct": 0.02, "take_profit_pct": 0.03},
        supported_timeframes=["15m", "1h", "4h", "1d"],
    ),
    "supertrend_atr": _template(
        name="Supertrend + ATR filter",
        family="momentum",
        thesis="A volatility-scaled trend flip is tradable when range is neither dead nor chaotic.",
        entry="Supertrend flips and ATR percent sits inside a band.",
        exit="Trail an ATR stop after the flip.",
        regime_filter="ATR percent must sit between a minimum and a maximum.",
        cost_sensitivity="medium",
        typical_holding_period="many bars, often days",
        suitable_regimes=["trending_low_vol", "trending_normal_vol", "trending_high_vol"],
        description="Follow Supertrend direction only when ATR percentage is within bounds.",
        required_indicators=["Supertrend", "ATR"],
        entry_conditions={
            "long": "Supertrend turns bullish and ATR percentage passes its filter",
            "short": "Supertrend turns bearish and ATR percentage passes its filter",
        },
        exit_conditions=["opposite_signal", "trailing_atr", "time_stop"],
        default_exit_rule="trailing_atr",
        default_trailing_atr_multiple=3.0,
        default_parameters={
            "atr_period": 10,
            "supertrend_multiplier": 3.0,
            "minimum_atr_pct": 0.002,
            "maximum_atr_pct": 0.08,
        },
        default_risk={"stop_loss_pct": 0.02, "take_profit_pct": None},
        supported_timeframes=list(_INTRADAY),
    ),
    "macd_htf_bias": _template(
        name="MACD histogram crossover with higher-timeframe bias",
        family="momentum",
        thesis="Take a lower-timeframe momentum cross only when the higher timeframe agrees.",
        entry="MACD histogram crosses zero in the direction of the higher-timeframe EMA bias.",
        exit="Time stop, opposite signal, or a fixed stop.",
        regime_filter="Higher-timeframe EMA bias must agree with the cross.",
        cost_sensitivity="medium",
        typical_holding_period="intraday to a couple of days",
        suitable_regimes=["trending_low_vol", "trending_normal_vol", "trending_high_vol"],
        description="Trade MACD histogram zero-crosses in the higher-timeframe EMA trend.",
        required_indicators=["MACD", "EMA"],
        entry_conditions={
            "long": "MACD histogram crosses positive with bullish higher-timeframe EMA bias",
            "short": "MACD histogram crosses negative with bearish higher-timeframe EMA bias",
        },
        default_exit_rule="time_stop",
        default_time_stop_bars=48,
        default_parameters={
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "higher_timeframe": "4h",
            "bias_fast_period": 20,
            "bias_slow_period": 50,
        },
        supported_timeframes=["5m", "15m", "1h"],
    ),
    "bollinger_squeeze": _template(
        name="Bollinger Band squeeze breakout",
        family="volatility",
        thesis="A compressed band stores a move. The break of that compression is the trade.",
        entry="Band width was compressed, then close leaves the band.",
        exit="Fixed reward or a stop back inside the range.",
        regime_filter="Prior bar must be a squeeze. Do not chase an already wide band.",
        cost_sensitivity="medium",
        typical_holding_period="a few bars after the break",
        suitable_regimes=[
            "choppy_low_vol",
            "choppy_normal_vol",
            "trending_low_vol",
            "trending_normal_vol",
            "trending_high_vol",
        ],
        description="Trade breakouts from a compressed Bollinger Band width.",
        required_indicators=["Bollinger Bands"],
        entry_conditions={
            "long": "prior band width is compressed and close breaks above the upper band",
            "short": "prior band width is compressed and close breaks below the lower band",
        },
        default_exit_rule="fixed_risk_reward",
        default_parameters={
            "bollinger_period": 20,
            "bollinger_deviations": 2.0,
            "squeeze_width_pct": 0.08,
        },
        supported_timeframes=["15m", "1h", "4h", "1d"],
    ),
    "breakout_confirmed": _template(
        name="Breakout with confirmation",
        family="breakout",
        thesis=(
            "A Donchian break is only an edge when range is expanding and volume confirms "
            "participation. Bare breaks in quiet tape are noise."
        ),
        entry="Close breaks the prior Donchian channel, ATR is expanding, and volume spikes.",
        exit="Stop back inside the broken range, or a fixed reward.",
        regime_filter="Require ATR expansion and a volume spike. Skip quiet breaks.",
        cost_sensitivity="medium",
        typical_holding_period="several bars through the expansion",
        suitable_regimes=["trending_high_vol", "choppy_high_vol", "trending_normal_vol"],
        description="Donchian break only when ATR expands and volume spikes.",
        required_indicators=["Donchian", "ATR", "Volume"],
        entry_conditions={
            "long": "close breaks the prior channel high with ATR expansion and a volume spike",
            "short": "close breaks the prior channel low with ATR expansion and a volume spike",
        },
        default_exit_rule="fixed_risk_reward",
        default_parameters={
            "channel_period": 20,
            "atr_period": 14,
            "volume_multiple": 1.5,
            "atr_expansion": 1.05,
        },
        default_risk={"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
        supported_timeframes=["15m", "1h", "4h", "1d"],
    ),
    "volatility_fade": _template(
        name="Volatility fade",
        family="volatility",
        thesis=(
            "After a band expansion, the first close back inside the band is often the "
            "exhaustion of that push, not the start of a trend."
        ),
        entry="Previous close was outside the Bollinger Band and the latest close returned inside.",
        exit="Mean re-entry or a stop if price leaves the band again.",
        regime_filter="Only fade after an expansion. Do not fade a fresh squeeze break.",
        cost_sensitivity="high",
        typical_holding_period="one to a few bars",
        suitable_regimes=["choppy_high_vol", "trending_high_vol"],
        description="Fade a Bollinger expansion once price closes back inside the band.",
        required_indicators=["Bollinger Bands"],
        entry_conditions={
            "long": "previous close was below the lower band and the latest close is back inside",
            "short": "previous close was above the upper band and the latest close is back inside",
        },
        default_exit_rule="fixed_risk_reward",
        default_parameters={"bollinger_period": 20, "bollinger_deviations": 2.0},
        default_risk={"stop_loss_pct": 0.02, "take_profit_pct": 0.03},
        supported_timeframes=["15m", "1h", "4h", "1d"],
    ),
    "carry_funding": _template(
        name="Carry / funding",
        family="carry",
        thesis=(
            "Perp funding is a financing spread. When funding is sufficiently negative, "
            "longs are paid to hold; when it is richly positive, shorts collect the carry."
        ),
        entry="Funding rate crosses a threshold. Spot uses the same rule as a bias.",
        exit="Funding normalizes, or a time stop.",
        regime_filter="Prefer stable or low-volatility regimes. Skip funding spikes in chaos.",
        cost_sensitivity="high",
        typical_holding_period="one funding window to a few days",
        suitable_regimes=["choppy_low_vol", "trending_low_vol", "choppy_normal_vol"],
        description="Enter when the funding rate crosses a carry threshold.",
        required_indicators=["Funding rate"],
        entry_conditions={
            "long": "funding crosses down through the negative threshold",
            "short": "funding crosses up through the positive threshold",
        },
        default_exit_rule="time_stop",
        default_time_stop_bars=24,
        default_parameters={"funding_threshold": 0.0001},
        default_risk={"stop_loss_pct": 0.02, "take_profit_pct": None},
        supported_timeframes=["15m", "1h", "4h", "1d"],
    ),
    "regime_gated_trend": _template(
        name="Regime-gated trend",
        family="regime_gated_trend",
        thesis=(
            "Moving-average crosses only pay when the market is actually trending. "
            "ADX is the gate that keeps the same cross out of chop."
        ),
        entry="Fast EMA crosses the slow EMA only while ADX is above the trend threshold.",
        exit="Opposite cross, ADX collapse is not required, stop or time stop handles the exit.",
        regime_filter="ADX must be at or above the configured minimum, default 25.",
        cost_sensitivity="medium",
        typical_holding_period="days while the trend persists",
        suitable_regimes=["trending_low_vol", "trending_normal_vol", "trending_high_vol"],
        description="EMA cross only when ADX confirms a trend.",
        required_indicators=["EMA", "ADX"],
        entry_conditions={
            "long": "fast EMA crosses above slow EMA and ADX is above the trend threshold",
            "short": "fast EMA crosses below slow EMA and ADX is above the trend threshold",
        },
        default_exit_rule="opposite_signal",
        default_parameters={
            "fast_period": 12,
            "slow_period": 26,
            "adx_period": 14,
            "adx_minimum": 25,
        },
        default_risk={"stop_loss_pct": 0.02, "take_profit_pct": None},
        supported_timeframes=list(_INTRADAY),
    ),
    "htf_trend_ltf_timing": _template(
        name="Higher-timeframe trend + lower-timeframe timing",
        family="multi_timeframe",
        thesis="The higher timeframe sets direction. The lower timeframe only chooses the entry.",
        entry="Lower-timeframe EMA cross in the direction of the higher-timeframe EMA trend.",
        exit="Time stop or a stop. The lower timeframe must not override the higher one.",
        regime_filter="Higher-timeframe trend must agree. A neutral higher timeframe stays flat.",
        cost_sensitivity="medium",
        typical_holding_period="intraday, bounded by a time stop",
        suitable_regimes=["trending_low_vol", "trending_normal_vol", "trending_high_vol"],
        description=(
            "Enter on a lower-timeframe EMA cross only in the higher-timeframe EMA trend."
        ),
        required_indicators=["EMA"],
        entry_conditions={
            "long": "lower-timeframe bullish EMA cross with bullish higher-timeframe EMA trend",
            "short": "lower-timeframe bearish EMA cross with bearish higher-timeframe EMA trend",
        },
        default_exit_rule="time_stop",
        default_time_stop_bars=24,
        default_parameters={
            "fast_period": 8,
            "slow_period": 21,
            "higher_timeframe": "4h",
            "bias_fast_period": 20,
            "bias_slow_period": 50,
        },
        supported_timeframes=["5m", "15m", "1h"],
    ),
    "multi_timeframe_hybrid": _template(
        name="Multi-timeframe hybrid",
        family="multi_timeframe",
        thesis=(
            "Daily or higher-timeframe direction is the bias. The lower timeframe should "
            "buy weakness or sell strength inside that bias, not chase another crossover."
        ),
        entry="Higher-timeframe EMA bias plus a lower-timeframe RSI extreme reverting back inside.",
        exit="RSI normalizes, a time stop, or a stop against the higher-timeframe bias.",
        regime_filter="Higher-timeframe bias is required. Flat when that bias is neutral.",
        cost_sensitivity="medium",
        typical_holding_period="intraday pullback inside a higher-timeframe swing",
        suitable_regimes=[
            "trending_low_vol",
            "trending_normal_vol",
            "trending_high_vol",
            "choppy_normal_vol",
        ],
        description="Higher-timeframe trend filter with lower-timeframe mean-reversion timing.",
        required_indicators=["EMA", "RSI"],
        entry_conditions={
            "long": "bullish higher timeframe and RSI crosses up from oversold",
            "short": "bearish higher timeframe and RSI crosses down from overbought",
        },
        default_exit_rule="time_stop",
        default_time_stop_bars=24,
        default_parameters={
            "rsi_period": 14,
            "oversold": 35,
            "overbought": 65,
            "higher_timeframe": "4h",
            "bias_fast_period": 20,
            "bias_slow_period": 50,
        },
        supported_timeframes=["5m", "15m", "1h"],
    ),
}


REFERENCE_EXAMPLES: list[dict[str, str]] = [
    {
        "family": "mean_reversion",
        "regime": "choppy_low_vol",
        "edge": (
            "Price is oscillating around a stable mean and trend strength is low, so fading "
            "a 1.5 standard-deviation stretch has a statistical reason to work. A pure EMA "
            "cross would churn the same range."
        ),
        "implementation": "20-bar z-score cross back through -1.5, stop beyond the extreme.",
    },
    {
        "family": "relative_value",
        "regime": "any, if the pair relationship holds",
        "edge": (
            "BTC versus ETH can dislocate from its recent ratio without either coin having "
            "started a durable trend. The trade is the ratio returning, not a directional bet."
        ),
        "implementation": "BTC/ETH ratio z-score crosses back through -1.5.",
    },
    {
        "family": "breakout",
        "regime": "trending_high_vol",
        "edge": (
            "A Donchian break during ATR expansion with a volume spike means new participation "
            "is paying through a level. The same break without expansion is usually a fake."
        ),
        "implementation": "20-bar channel break, ATR above its own average, volume above 1.5x.",
    },
]


def strategy_library_summary() -> list[dict[str, Any]]:
    return [
        {
            "template_name": template_name,
            "name": template["name"],
            "family": template["family"],
            "thesis": template["thesis"],
            "entry": template["entry"],
            "exit": template["exit"],
            "regime_filter": template["regime_filter"],
            "cost_sensitivity": template["cost_sensitivity"],
            "typical_holding_period": template["typical_holding_period"],
            "suitable_regimes": template["suitable_regimes"],
            "pure_momentum": template["pure_momentum"],
            "description": template["description"],
            "required_indicators": template["required_indicators"],
            "entry_conditions": template["entry_conditions"],
            "exit_conditions": template["exit_conditions"],
            "default_parameters": template["default_parameters"],
            "default_risk": template["default_risk"],
            "default_exit_rule": template["default_exit_rule"],
            "default_time_stop_bars": template.get("default_time_stop_bars"),
            "default_trailing_atr_multiple": template.get("default_trailing_atr_multiple"),
            "supported_timeframes": template["supported_timeframes"],
        }
        for template_name, template in STRATEGY_LIBRARY.items()
    ]


def strategy_fingerprint(strategy: StrategyDefinition) -> str:
    return json.dumps(
        {
            "template_name": strategy.template_name,
            "chosen_timeframe": strategy.chosen_timeframe,
            "parameters": strategy.parameters,
            "exit_rule": strategy.exit_rule,
            "stop_loss_pct": strategy.stop_loss_pct,
            "take_profit_pct": strategy.take_profit_pct,
            "time_stop_bars": strategy.time_stop_bars,
            "trailing_atr_multiple": strategy.trailing_atr_multiple,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
