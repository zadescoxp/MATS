from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from framework.schemas.models import LiveSignal, OHLCVBar, StrategyDefinition
from framework.tools.technicals import atr, bollinger_bands, ema, macd, rsi, supertrend


def evaluate_live_signal(
    strategy_def: StrategyDefinition | Mapping[str, Any],
    latest_ohlcv_dict: Mapping[str, Sequence[OHLCVBar | Mapping[str, Any]]],
    position: Mapping[str, Any] | None = None,
) -> LiveSignal:
    """Evaluate the final candle using the strategy's deterministic library template."""
    strategy = (
        strategy_def
        if isinstance(strategy_def, StrategyDefinition)
        else StrategyDefinition.model_validate(strategy_def)
    )
    timeframe = strategy.chosen_timeframe
    candles = latest_ohlcv_dict.get(timeframe)
    if not candles:
        raise ValueError(f"No OHLCV data for strategy timeframe {timeframe!r}")
    frame = _frame(candles)
    if len(frame) < 2:
        raise ValueError("Live signal evaluation requires at least two candles")
    close = frame["close"]
    params = strategy.parameters
    long_signal = False
    short_signal = False
    reason = "No complete entry condition matched."
    current_atr: float | None = None

    if strategy.template_name == "ema_crossover":
        fast_period = int(params.get("fast_period", strategy.fast_period))
        slow_period = int(params.get("slow_period", strategy.slow_period))
        fast = ema(close, fast_period)
        slow = ema(close, slow_period)
        long_signal = _crossed_up(fast, slow)
        short_signal = _crossed_down(fast, slow)
        for rule in strategy.filters:
            if rule == "rsi_below_70":
                current_rsi = rsi(close).iloc[-1]
                long_signal = long_signal and _finite(current_rsi) and current_rsi < 70
                short_signal = short_signal and _finite(current_rsi) and current_rsi < 70
            elif rule == "price_above_slow_ema":
                above_slow = _finite(slow.iloc[-1]) and close.iloc[-1] > slow.iloc[-1]
                long_signal = long_signal and above_slow
                short_signal = short_signal and above_slow
            elif rule == "volume_positive":
                long_signal = long_signal and frame["volume"].iloc[-1] > 0
                short_signal = short_signal and frame["volume"].iloc[-1] > 0
        reason = (
            "Fast EMA crossed above slow EMA."
            if long_signal
            else ("Fast EMA crossed below slow EMA." if short_signal else reason)
        )
    elif strategy.template_name == "rsi_mean_reversion":
        period = int(params.get("rsi_period", 14))
        oversold = float(params.get("oversold", 30))
        overbought = float(params.get("overbought", 70))
        trend_period = int(params.get("trend_period", 50))
        strength = rsi(close, period)
        trend = ema(close, trend_period)
        long_signal = (
            _finite(strength.iloc[-2])
            and _finite(strength.iloc[-1])
            and strength.iloc[-2] <= oversold < strength.iloc[-1]
            and _finite(trend.iloc[-1])
            and close.iloc[-1] > trend.iloc[-1]
        )
        short_signal = (
            _finite(strength.iloc[-2])
            and _finite(strength.iloc[-1])
            and strength.iloc[-2] >= overbought > strength.iloc[-1]
            and _finite(trend.iloc[-1])
            and close.iloc[-1] < trend.iloc[-1]
        )
        reason = (
            "RSI recovered above oversold in an uptrend."
            if long_signal
            else ("RSI fell below overbought in a downtrend." if short_signal else reason)
        )
    elif strategy.template_name == "supertrend_atr":
        period = int(params.get("atr_period", 10))
        current_atr = _last_finite(atr(frame, period))
        trend = supertrend(frame, period, float(params.get("supertrend_multiplier", 3.0)))[
            "direction"
        ]
        atr_pct = current_atr / close.iloc[-1] if current_atr is not None else None
        min_atr = float(params.get("minimum_atr_pct", 0.002))
        max_atr = float(params.get("maximum_atr_pct", 0.08))
        filter_passes = atr_pct is not None and min_atr <= atr_pct <= max_atr
        long_signal = trend.iloc[-2] < 1 and trend.iloc[-1] == 1 and filter_passes
        short_signal = trend.iloc[-2] > -1 and trend.iloc[-1] == -1 and filter_passes
        reason = (
            "Supertrend turned bullish and ATR filter passed."
            if long_signal
            else ("Supertrend turned bearish and ATR filter passed." if short_signal else reason)
        )
    elif strategy.template_name == "macd_htf_bias":
        fast_period = int(params.get("macd_fast", 12))
        slow_period = int(params.get("macd_slow", 26))
        signal_period = int(params.get("macd_signal", 9))
        histogram = macd(close, fast_period, slow_period, signal_period)["histogram"]
        higher = _higher_timeframe_bias(strategy, latest_ohlcv_dict)
        long_signal = _crossed_above_zero(histogram) and higher == "bullish"
        short_signal = _crossed_below_zero(histogram) and higher == "bearish"
        if long_signal:
            reason = "MACD histogram crossed positive with bullish higher-timeframe bias."
        elif short_signal:
            reason = "MACD histogram crossed negative with bearish higher-timeframe bias."
        elif higher == "neutral":
            reason = "MACD crossover and higher-timeframe bias do not align."
    elif strategy.template_name == "bollinger_squeeze":
        period = int(params.get("bollinger_period", 20))
        deviations = float(params.get("bollinger_deviations", 2.0))
        bands = bollinger_bands(close, period, deviations)
        widths = (bands["upper"] - bands["lower"]) / bands["middle"]
        squeeze_width = float(params.get("squeeze_width_pct", 0.08))
        squeezed = _finite(widths.iloc[-2]) and widths.iloc[-2] <= squeeze_width
        long_signal = (
            squeezed
            and close.iloc[-2] <= bands["upper"].iloc[-2]
            and close.iloc[-1] > bands["upper"].iloc[-1]
        )
        short_signal = (
            squeezed
            and close.iloc[-2] >= bands["lower"].iloc[-2]
            and close.iloc[-1] < bands["lower"].iloc[-1]
        )
        reason = (
            "Close broke above the upper Bollinger Band after a squeeze."
            if long_signal
            else (
                "Close broke below the lower Bollinger Band after a squeeze."
                if short_signal
                else reason
            )
        )
    elif strategy.template_name == "htf_trend_ltf_timing":
        fast_period = int(params.get("fast_period", strategy.fast_period))
        slow_period = int(params.get("slow_period", strategy.slow_period))
        fast = ema(close, fast_period)
        slow = ema(close, slow_period)
        higher = _higher_timeframe_bias(strategy, latest_ohlcv_dict)
        long_signal = _crossed_up(fast, slow) and higher == "bullish"
        short_signal = _crossed_down(fast, slow) and higher == "bearish"
        if long_signal:
            reason = "Lower-timeframe bullish crossover aligned with higher-timeframe trend."
        elif short_signal:
            reason = "Lower-timeframe bearish crossover aligned with higher-timeframe trend."
        elif higher == "neutral":
            reason = "Lower-timeframe timing and higher-timeframe trend do not align."

    if strategy.exit_rule == "trailing_atr" and current_atr is None:
        current_atr = _last_finite(atr(frame, int(params.get("atr_period", 14))))
    price = float(close.iloc[-1])
    action: str = "long" if long_signal else "short" if short_signal else "hold"
    stop = _initial_stop(strategy, price, current_atr, action)
    strength = 0.8 if long_signal or short_signal else 0.0
    if position is not None:
        action, reason, stop = _evaluate_position_exit(
            strategy, frame, position, action, reason, stop, current_atr
        )
        if action == "exit":
            strength = 1.0
    return LiveSignal(
        action=action,
        reason=reason,
        strength=strength,
        price=price,
        stop=stop,
        strategy_over=action == "exit",
    )


def _frame(candles: Sequence[OHLCVBar | Mapping[str, Any]]) -> pd.DataFrame:
    rows = [
        candle.model_dump(mode="python") if isinstance(candle, OHLCVBar) else dict(candle)
        for candle in candles
    ]
    frame = pd.DataFrame(rows)
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"OHLCV data is missing columns: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("At least one OHLCV candle is required")
    frame = frame.astype({key: "float64" for key in ("open", "high", "low", "close", "volume")})
    if not frame["close"].map(np.isfinite).all():
        raise ValueError("OHLCV data contains non-finite prices")
    return frame


def _higher_timeframe_bias(
    strategy: StrategyDefinition,
    data: Mapping[str, Sequence[OHLCVBar | Mapping[str, Any]]],
) -> str:
    timeframe = str(strategy.parameters.get("higher_timeframe", "4h"))
    candles = data.get(timeframe)
    if not candles:
        return "neutral"
    frame = _frame(candles)
    fast_period = int(strategy.parameters.get("bias_fast_period", 20))
    slow_period = int(strategy.parameters.get("bias_slow_period", 50))
    if len(frame) < slow_period:
        return "neutral"
    fast = ema(frame["close"], fast_period).iloc[-1]
    slow = ema(frame["close"], slow_period).iloc[-1]
    return "bullish" if fast > slow else "bearish" if fast < slow else "neutral"


def _evaluate_position_exit(
    strategy: StrategyDefinition,
    frame: pd.DataFrame,
    position: Mapping[str, Any],
    signal_action: str,
    signal_reason: str,
    suggested_stop: float | None,
    current_atr: float | None,
) -> tuple[str, str, float | None]:
    side = str(position.get("side", "long"))
    entry_price = float(position.get("entry_price", frame["close"].iloc[-1]))
    stop = float(position.get("stop_price", suggested_stop or entry_price))
    take_profit = (
        float(position["take_profit_price"])
        if position.get("take_profit_price") is not None
        else (
            entry_price * (1 + strategy.take_profit_pct)
            if side == "long" and strategy.take_profit_pct
            else entry_price * (1 - strategy.take_profit_pct)
            if strategy.take_profit_pct
            else None
        )
    )
    bar = frame.iloc[-1]
    if side == "long":
        if bar["open"] <= stop or bar["low"] <= stop:
            return "exit", "Protective stop was hit.", stop
        if take_profit is not None and (bar["open"] >= take_profit or bar["high"] >= take_profit):
            return "exit", "Take-profit target was hit.", stop
        opposite = signal_action == "short"
    else:
        if bar["open"] >= stop or bar["high"] >= stop:
            return "exit", "Protective stop was hit.", stop
        if take_profit is not None and (bar["open"] <= take_profit or bar["low"] <= take_profit):
            return "exit", "Take-profit target was hit.", stop
        opposite = signal_action == "long"

    bars_held = int(position.get("bars_held", 0))
    if strategy.time_stop_bars and bars_held >= strategy.time_stop_bars:
        return "exit", "Configured time stop was reached.", stop
    if strategy.exit_rule == "trailing_atr" and current_atr:
        multiple = strategy.trailing_atr_multiple or float(
            strategy.parameters.get("trailing_atr_multiple", 3.0)
        )
        anchor_key = "peak_price" if side == "long" else "trough_price"
        anchor = float(position.get(anchor_key, bar["close"]))
        trailing_stop = (
            anchor - current_atr * multiple if side == "long" else anchor + current_atr * multiple
        )
        if side == "long" and bar["low"] <= trailing_stop:
            return "exit", "Trailing ATR stop was hit.", trailing_stop
        if side == "short" and bar["high"] >= trailing_stop:
            return "exit", "Trailing ATR stop was hit.", trailing_stop
    if opposite:
        return "exit", f"Opposite live signal: {signal_reason}", stop
    return ("hold", "Position remains within its exit rules.", stop)


def _initial_stop(
    strategy: StrategyDefinition, price: float, current_atr: float | None, action: str
) -> float:
    if strategy.exit_rule == "trailing_atr" and current_atr:
        multiple = strategy.trailing_atr_multiple or float(
            strategy.parameters.get("trailing_atr_multiple", 3.0)
        )
        return (
            price + current_atr * multiple if action == "short" else price - current_atr * multiple
        )
    if action == "short":
        return price * (1 + strategy.stop_loss_pct)
    return price * (1 - strategy.stop_loss_pct)


def _crossed_up(left: pd.Series, right: pd.Series) -> bool:
    return (
        _finite(left.iloc[-2])
        and _finite(right.iloc[-2])
        and _finite(left.iloc[-1])
        and _finite(right.iloc[-1])
        and left.iloc[-2] <= right.iloc[-2]
        and left.iloc[-1] > right.iloc[-1]
    )


def _crossed_down(left: pd.Series, right: pd.Series) -> bool:
    return (
        _finite(left.iloc[-2])
        and _finite(right.iloc[-2])
        and _finite(left.iloc[-1])
        and _finite(right.iloc[-1])
        and left.iloc[-2] >= right.iloc[-2]
        and left.iloc[-1] < right.iloc[-1]
    )


def _crossed_above_zero(values: pd.Series) -> bool:
    return (
        _finite(values.iloc[-2])
        and _finite(values.iloc[-1])
        and values.iloc[-2] <= 0 < values.iloc[-1]
    )


def _crossed_below_zero(values: pd.Series) -> bool:
    return (
        _finite(values.iloc[-2])
        and _finite(values.iloc[-1])
        and values.iloc[-2] >= 0 > values.iloc[-1]
    )


def _last_finite(values: pd.Series) -> float | None:
    value = values.iloc[-1]
    return float(value) if _finite(value) else None


def _finite(value: Any) -> bool:
    return pd.notna(value) and np.isfinite(value)
