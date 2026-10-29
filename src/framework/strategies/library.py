from typing import Any

STRATEGY_LIBRARY: dict[str, dict[str, Any]] = {
    "ema_crossover": {
        "name": "EMA crossover",
        "description": "Trade fast/slow EMA crossovers with ATR-aware risk controls.",
        "required_indicators": ["EMA"],
        "entry_conditions": {
            "long": "fast EMA crosses above slow EMA",
            "short": "fast EMA crosses below slow EMA",
        },
        "exit_conditions": ["opposite_signal", "stop_loss", "take_profit", "time_stop"],
        "default_parameters": {"fast_period": 12, "slow_period": 26},
        "default_risk": {"stop_loss_pct": 0.02, "take_profit_pct": None},
        "supported_timeframes": ["5m", "15m", "1h", "4h", "1d"],
    },
    "rsi_mean_reversion": {
        "name": "RSI mean-reversion / trend pullback",
        "description": "Enter on RSI recovery from an extreme in the EMA trend direction.",
        "required_indicators": ["RSI", "EMA"],
        "entry_conditions": {
            "long": "close above trend EMA and RSI crosses above oversold threshold",
            "short": "close below trend EMA and RSI crosses below overbought threshold",
        },
        "exit_conditions": ["opposite_signal", "stop_loss", "take_profit", "time_stop"],
        "default_parameters": {
            "rsi_period": 14,
            "oversold": 30,
            "overbought": 70,
            "trend_period": 50,
        },
        "default_risk": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
        "supported_timeframes": ["15m", "1h", "4h", "1d"],
    },
    "supertrend_atr": {
        "name": "Supertrend + ATR filter",
        "description": "Follow Supertrend direction only when ATR percentage is within bounds.",
        "required_indicators": ["Supertrend", "ATR"],
        "entry_conditions": {
            "long": "Supertrend turns bullish and ATR percentage passes its filter",
            "short": "Supertrend turns bearish and ATR percentage passes its filter",
        },
        "exit_conditions": ["opposite_signal", "trailing_atr", "time_stop"],
        "default_parameters": {
            "atr_period": 10,
            "supertrend_multiplier": 3.0,
            "minimum_atr_pct": 0.002,
            "maximum_atr_pct": 0.08,
        },
        "default_risk": {"stop_loss_pct": 0.02, "take_profit_pct": None},
        "supported_timeframes": ["5m", "15m", "1h", "4h", "1d"],
    },
    "macd_htf_bias": {
        "name": "MACD histogram crossover with higher-timeframe bias",
        "description": "Trade MACD histogram zero-crosses in the higher-timeframe EMA trend.",
        "required_indicators": ["MACD", "EMA"],
        "entry_conditions": {
            "long": "MACD histogram crosses positive with bullish higher-timeframe EMA bias",
            "short": "MACD histogram crosses negative with bearish higher-timeframe EMA bias",
        },
        "exit_conditions": ["opposite_signal", "stop_loss", "take_profit", "time_stop"],
        "default_parameters": {
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "higher_timeframe": "4h",
            "bias_fast_period": 20,
            "bias_slow_period": 50,
        },
        "default_risk": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
        "supported_timeframes": ["5m", "15m", "1h"],
    },
    "bollinger_squeeze": {
        "name": "Bollinger Band squeeze breakout",
        "description": "Trade breakouts from a compressed Bollinger Band width.",
        "required_indicators": ["Bollinger Bands"],
        "entry_conditions": {
            "long": "prior band width is compressed and close breaks above the upper band",
            "short": "prior band width is compressed and close breaks below the lower band",
        },
        "exit_conditions": ["opposite_signal", "stop_loss", "take_profit", "time_stop"],
        "default_parameters": {
            "bollinger_period": 20,
            "bollinger_deviations": 2.0,
            "squeeze_width_pct": 0.08,
        },
        "default_risk": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
        "supported_timeframes": ["15m", "1h", "4h", "1d"],
    },
    "htf_trend_ltf_timing": {
        "name": "Higher-timeframe trend + lower-timeframe timing",
        "description": (
            "Enter on a lower-timeframe EMA cross only in the higher-timeframe EMA trend."
        ),
        "required_indicators": ["EMA"],
        "entry_conditions": {
            "long": "lower-timeframe bullish EMA cross with bullish higher-timeframe EMA trend",
            "short": "lower-timeframe bearish EMA cross with bearish higher-timeframe EMA trend",
        },
        "exit_conditions": ["opposite_signal", "stop_loss", "take_profit", "time_stop"],
        "default_parameters": {
            "fast_period": 8,
            "slow_period": 21,
            "higher_timeframe": "4h",
            "bias_fast_period": 20,
            "bias_slow_period": 50,
        },
        "default_risk": {"stop_loss_pct": 0.02, "take_profit_pct": 0.04},
        "supported_timeframes": ["5m", "15m", "1h"],
    },
}


def strategy_library_summary() -> list[dict[str, Any]]:
    return [
        {
            "template_name": template_name,
            "name": template["name"],
            "description": template["description"],
            "required_indicators": template["required_indicators"],
            "entry_conditions": template["entry_conditions"],
            "exit_conditions": template["exit_conditions"],
            "default_parameters": template["default_parameters"],
            "default_risk": template["default_risk"],
            "supported_timeframes": template["supported_timeframes"],
        }
        for template_name, template in STRATEGY_LIBRARY.items()
    ]
