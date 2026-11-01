from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from framework.schemas.models import LiveSignal, OHLCVBar, StrategyDefinition
from framework.tools.technicals import (
    adx,
    atr,
    bollinger_bands,
    donchian,
    ema,
    macd,
    rsi,
    sma,
    supertrend,
)

# Candles per timeframe a live signal is evaluated on. Backtests evaluate each bar on the
# same trailing window so a backtested signal is the signal live trading would have seen.
LIVE_SIGNAL_BARS = 500


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
    elif strategy.template_name == "mean_reversion":
        long_signal, short_signal, reason = _mean_reversion(close, params)
    elif strategy.template_name == "stat_arb_spread":
        long_signal, short_signal, reason = _stat_arb(frame, latest_ohlcv_dict, params)
    elif strategy.template_name == "breakout_confirmed":
        long_signal, short_signal, reason, current_atr = _confirmed_breakout(frame, params)
    elif strategy.template_name == "volatility_fade":
        long_signal, short_signal, reason = _volatility_fade(close, params)
    elif strategy.template_name == "carry_funding":
        long_signal, short_signal, reason = _carry_funding(latest_ohlcv_dict, params)
    elif strategy.template_name == "regime_gated_trend":
        fast_period = int(params.get("fast_period", strategy.fast_period))
        slow_period = int(params.get("slow_period", strategy.slow_period))
        fast = ema(close, fast_period)
        slow = ema(close, slow_period)
        trend_gate = _adx_value(frame, int(params.get("adx_period", 14)))
        minimum = float(params.get("adx_minimum", 25))
        gated = trend_gate is not None and trend_gate >= minimum
        long_signal = _crossed_up(fast, slow) and gated
        short_signal = _crossed_down(fast, slow) and gated
        if long_signal:
            reason = f"EMA cross confirmed by ADX {trend_gate:.1f}."
        elif short_signal:
            reason = f"EMA cross confirmed by ADX {trend_gate:.1f}."
        elif _crossed_up(fast, slow) or _crossed_down(fast, slow):
            reason = "EMA cross ignored because ADX is below the trend gate."
    elif strategy.template_name == "multi_timeframe_hybrid":
        period = int(params.get("rsi_period", 14))
        oversold = float(params.get("oversold", 35))
        overbought = float(params.get("overbought", 65))
        strength = rsi(close, period)
        higher = _higher_timeframe_bias(strategy, latest_ohlcv_dict)
        long_signal = _crossed_up_level(strength, oversold) and higher == "bullish"
        short_signal = _crossed_down_level(strength, overbought) and higher == "bearish"
        if long_signal:
            reason = "Lower-timeframe RSI reverted inside a bullish higher-timeframe bias."
        elif short_signal:
            reason = "Lower-timeframe RSI reverted inside a bearish higher-timeframe bias."
        elif higher == "neutral":
            reason = "Higher-timeframe bias is neutral, so the hybrid stays flat."

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
    same_entry_candle = False
    entry_time = position.get("opened_at")
    bar_time = bar.get("timestamp")
    if entry_time is not None and bar_time is not None:
        same_entry_candle = pd.Timestamp(bar_time) <= pd.Timestamp(entry_time)
    if side == "long":
        stop_hit = (
            bar["close"] <= stop if same_entry_candle else bar["open"] <= stop or bar["low"] <= stop
        )
        if stop_hit:
            return "exit", "Protective stop was hit.", stop
        target_hit = (
            bar["close"] >= take_profit
            if same_entry_candle and take_profit is not None
            else take_profit is not None
            and (bar["open"] >= take_profit or bar["high"] >= take_profit)
        )
        if target_hit:
            return "exit", "Take-profit target was hit.", stop
        opposite = signal_action == "short"
    else:
        stop_hit = (
            bar["close"] >= stop
            if same_entry_candle
            else bar["open"] >= stop or bar["high"] >= stop
        )
        if stop_hit:
            return "exit", "Protective stop was hit.", stop
        target_hit = (
            bar["close"] <= take_profit
            if same_entry_candle and take_profit is not None
            else take_profit is not None
            and (bar["open"] <= take_profit or bar["low"] <= take_profit)
        )
        if target_hit:
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
        if side == "long" and (
            bar["close"] <= trailing_stop if same_entry_candle else bar["low"] <= trailing_stop
        ):
            return "exit", "Trailing ATR stop was hit.", trailing_stop
        if side == "short" and (
            bar["close"] >= trailing_stop if same_entry_candle else bar["high"] >= trailing_stop
        ):
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


def _mean_reversion(close: pd.Series, params: Mapping[str, Any]) -> tuple[bool, bool, str]:
    period = int(params.get("z_period", 20))
    entry = float(params.get("z_entry", 1.5))
    score = _zscore(close, period)
    rsi_period = int(params.get("rsi_period", 2))
    extreme = float(params.get("rsi_extreme", 10))
    strength = rsi(close, rsi_period)
    z_long = _crossed_up_level(score, -entry)
    z_short = _crossed_down_level(score, entry)
    rsi_long = _crossed_up_level(strength, extreme)
    rsi_short = _crossed_down_level(strength, 100 - extreme)
    previous = close.iloc[-2]
    latest = close.iloc[-1]
    reclaimed = _finite(latest) and _finite(previous) and latest > previous
    lost = _finite(latest) and _finite(previous) and latest < previous
    long_signal = (z_long or rsi_long) and reclaimed
    short_signal = (z_short or rsi_short) and lost
    if long_signal:
        reason = "Price reclaimed after a downside extreme."
    elif short_signal:
        reason = "Price fell back after an upside extreme."
    else:
        reason = "No complete entry condition matched."
    return long_signal, short_signal, reason


def _stat_arb(
    frame: pd.DataFrame,
    data: Mapping[str, Sequence[OHLCVBar | Mapping[str, Any]]],
    params: Mapping[str, Any],
) -> tuple[bool, bool, str]:
    pair = data.get("pair")
    if not pair:
        return False, False, "Pair series is unavailable, so relative value stays flat."
    pair_frame = _frame(pair)
    ratio = _aligned_ratio(frame, pair_frame)
    if ratio is None or len(ratio) < 5:
        return False, False, "Pair series does not overlap enough to score the ratio."
    entry = float(params.get("z_entry", 1.5))
    score = _zscore(ratio, int(params.get("z_period", 30)))
    long_signal = _crossed_up_level(score, -entry)
    short_signal = _crossed_down_level(score, entry)
    if long_signal:
        reason = "Ratio z-score reverted up from a cheap extreme."
    elif short_signal:
        reason = "Ratio z-score reverted down from a rich extreme."
    else:
        reason = "No complete entry condition matched."
    return long_signal, short_signal, reason


def _confirmed_breakout(
    frame: pd.DataFrame, params: Mapping[str, Any]
) -> tuple[bool, bool, str, float | None]:
    period = int(params.get("channel_period", 20))
    channel = donchian(frame, period)
    average_range = atr(frame, int(params.get("atr_period", 14)))
    current_atr = _last_finite(average_range)
    volume_mean = sma(frame["volume"], period)
    expansion = float(params.get("atr_expansion", 1.05))
    volume_multiple = float(params.get("volume_multiple", 1.5))
    atr_mean = average_range.rolling(period, min_periods=period).mean()
    expanding = (
        _finite(average_range.iloc[-1])
        and _finite(atr_mean.iloc[-1])
        and average_range.iloc[-1] >= atr_mean.iloc[-1] * expansion
    )
    volume_spike = (
        _finite(volume_mean.iloc[-1])
        and frame["volume"].iloc[-1] >= volume_mean.iloc[-1] * volume_multiple
    )
    prior_high = channel["upper"].iloc[-2]
    prior_low = channel["lower"].iloc[-2]
    long_signal = (
        _finite(prior_high) and frame["close"].iloc[-1] > prior_high and expanding and volume_spike
    )
    short_signal = (
        _finite(prior_low) and frame["close"].iloc[-1] < prior_low and expanding and volume_spike
    )
    if long_signal:
        reason = "Donchian break confirmed by ATR expansion and a volume spike."
    elif short_signal:
        reason = "Donchian breakdown confirmed by ATR expansion and a volume spike."
    else:
        reason = "No complete entry condition matched."
    return long_signal, short_signal, reason, current_atr


def _volatility_fade(close: pd.Series, params: Mapping[str, Any]) -> tuple[bool, bool, str]:
    period = int(params.get("bollinger_period", 20))
    bands = bollinger_bands(close, period, float(params.get("bollinger_deviations", 2.0)))
    long_signal = (
        _finite(bands["lower"].iloc[-2])
        and close.iloc[-2] < bands["lower"].iloc[-2]
        and _finite(bands["lower"].iloc[-1])
        and bands["lower"].iloc[-1] <= close.iloc[-1] <= bands["upper"].iloc[-1]
    )
    short_signal = (
        _finite(bands["upper"].iloc[-2])
        and close.iloc[-2] > bands["upper"].iloc[-2]
        and _finite(bands["upper"].iloc[-1])
        and bands["lower"].iloc[-1] <= close.iloc[-1] <= bands["upper"].iloc[-1]
    )
    if long_signal:
        reason = "Close returned inside the band after a downside expansion."
    elif short_signal:
        reason = "Close returned inside the band after an upside expansion."
    else:
        reason = "No complete entry condition matched."
    return long_signal, short_signal, reason


def _carry_funding(
    data: Mapping[str, Sequence[OHLCVBar | Mapping[str, Any]]],
    params: Mapping[str, Any],
) -> tuple[bool, bool, str]:
    series = data.get("funding")
    if not series or len(series) < 2:
        return False, False, "Funding history is unavailable, so carry stays flat."
    rates = [_funding_rate(item) for item in series]
    if any(rate is None for rate in rates[-2:]):
        return False, False, "Funding history is unavailable, so carry stays flat."
    threshold = abs(float(params.get("funding_threshold", 0.0001)))
    previous, latest = rates[-2], rates[-1]
    assert previous is not None and latest is not None
    long_signal = previous > -threshold >= latest
    short_signal = previous < threshold <= latest
    if long_signal:
        reason = "Funding crossed into a negative carry that pays the long."
    elif short_signal:
        reason = "Funding crossed into a positive carry that pays the short."
    else:
        reason = "No complete entry condition matched."
    return long_signal, short_signal, reason


def _funding_rate(item: OHLCVBar | Mapping[str, Any]) -> float | None:
    close = item.close if isinstance(item, OHLCVBar) else item.get("close")
    if close is None or not _finite(close):
        return None
    return float(close) - 1


def _aligned_ratio(frame: pd.DataFrame, pair: pd.DataFrame) -> pd.Series | None:
    if "timestamp" not in frame.columns or "timestamp" not in pair.columns:
        size = min(len(frame), len(pair))
        if size < 5:
            return None
        denominator = pair["close"].iloc[-size:].reset_index(drop=True).replace(0, np.nan)
        return frame["close"].iloc[-size:].reset_index(drop=True) / denominator
    left = frame[["timestamp", "close"]].copy()
    right = pair[["timestamp", "close"]].rename(columns={"close": "pair_close"})
    merged = left.merge(right, on="timestamp", how="inner")
    if len(merged) < 5:
        return None
    return merged["close"] / merged["pair_close"].replace(0, np.nan)


def _zscore(close: pd.Series, period: int) -> pd.Series:
    mean = sma(close, period)
    deviation = close.rolling(period, min_periods=period).std(ddof=0).replace(0, np.nan)
    return (close - mean) / deviation


def _adx_value(frame: pd.DataFrame, period: int) -> float | None:
    value = adx(frame, period).iloc[-1]["adx"]
    return float(value) if _finite(value) else None


def _crossed_up_level(values: pd.Series, level: float) -> bool:
    return (
        len(values) >= 2
        and _finite(values.iloc[-2])
        and _finite(values.iloc[-1])
        and values.iloc[-2] <= level < values.iloc[-1]
    )


def _crossed_down_level(values: pd.Series, level: float) -> bool:
    return (
        len(values) >= 2
        and _finite(values.iloc[-2])
        and _finite(values.iloc[-1])
        and values.iloc[-2] >= level > values.iloc[-1]
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
