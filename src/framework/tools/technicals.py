from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel

from framework.schemas.models import OHLCVBar


class TechnicalSnapshot(BaseModel):
    timeframe: str
    values: dict[str, dict[str, float | None]]
    signals: dict[str, str]


def _series(candles: Sequence[OHLCVBar] | pd.DataFrame) -> pd.DataFrame:
    frame = (
        candles.copy()
        if isinstance(candles, pd.DataFrame)
        else pd.DataFrame([bar.model_dump() for bar in candles])
    )
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"OHLCV data is missing columns: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("At least one OHLCV candle is required")
    return frame.astype(
        {name: "float64" for name in ("open", "high", "low", "close", "volume")}
    )


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": signal_line, "histogram": line - signal_line})


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative_strength = gain / loss.replace(0, np.nan)
    result = 100 - 100 / (1 + relative_strength)
    return result.mask((loss == 0) & (gain > 0), 100).mask((gain == 0) & (loss > 0), 0)


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14, smooth: int = 3
) -> pd.DataFrame:
    lowest = low.rolling(period, min_periods=period).min()
    highest = high.rolling(period, min_periods=period).max()
    k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    return pd.DataFrame({"k": k, "d": k.rolling(smooth, min_periods=smooth).mean()})


def bollinger_bands(close: pd.Series, period: int = 20, deviations: float = 2) -> pd.DataFrame:
    middle = sma(close, period)
    width = close.rolling(period, min_periods=period).std(ddof=0) * deviations
    return pd.DataFrame({"upper": middle + width, "middle": middle, "lower": middle - width})


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift()
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(frame).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    average_range = atr(frame, period).replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / average_range
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / average_range
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame(
        {
            "adx": dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean(),
            "plus_di": plus_di,
            "minus_di": minus_di,
        }
    )


def supertrend(frame: pd.DataFrame, period: int = 10, multiplier: float = 3) -> pd.DataFrame:
    average_range = atr(frame, period)
    middle = (frame["high"] + frame["low"]) / 2
    upper = middle + multiplier * average_range
    lower = middle - multiplier * average_range
    trend = pd.Series(np.nan, index=frame.index, dtype="float64")
    direction = pd.Series(0, index=frame.index, dtype="int64")
    for index in range(1, len(frame)):
        if pd.isna(upper.iloc[index]) or pd.isna(lower.iloc[index]):
            continue
        prior_trend = trend.iloc[index - 1]
        prior_direction = direction.iloc[index - 1] or 1
        if frame["close"].iloc[index] > upper.iloc[index - 1]:
            direction.iloc[index] = 1
        elif frame["close"].iloc[index] < lower.iloc[index - 1]:
            direction.iloc[index] = -1
        else:
            direction.iloc[index] = prior_direction
            if direction.iloc[index] > 0:
                lower.iloc[index] = max(lower.iloc[index], lower.iloc[index - 1])
            else:
                upper.iloc[index] = min(upper.iloc[index], upper.iloc[index - 1])
        trend.iloc[index] = lower.iloc[index] if direction.iloc[index] > 0 else upper.iloc[index]
    return pd.DataFrame({"supertrend": trend, "direction": direction})


def vwap(frame: pd.DataFrame) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    cumulative_volume = frame["volume"].cumsum()
    return (typical * frame["volume"]).cumsum() / cumulative_volume.replace(0, np.nan)


def obv(frame: pd.DataFrame) -> pd.Series:
    direction = np.sign(frame["close"].diff()).fillna(0)
    return (direction * frame["volume"]).cumsum()


def cci(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    mean = typical.rolling(period, min_periods=period).mean()
    deviation = typical.rolling(period, min_periods=period).apply(
        lambda values: np.mean(np.abs(values - values.mean())), raw=True
    )
    return (typical - mean) / (0.015 * deviation).replace(0, np.nan)


def roc(close: pd.Series, period: int = 12) -> pd.Series:
    return close.pct_change(periods=period) * 100


def donchian(frame: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "upper": frame["high"].rolling(period, min_periods=period).max(),
            "middle": (
                frame["high"].rolling(period, min_periods=period).max()
                + frame["low"].rolling(period, min_periods=period).min()
            )
            / 2,
            "lower": frame["low"].rolling(period, min_periods=period).min(),
        }
    )


def keltner(
    frame: pd.DataFrame, period: int = 20, atr_period: int = 10, multiplier: float = 2
) -> pd.DataFrame:
    middle = ema(frame["close"], period)
    width = atr(frame, atr_period) * multiplier
    return pd.DataFrame({"upper": middle + width, "middle": middle, "lower": middle - width})


def compute_indicators(
    candles: Sequence[OHLCVBar] | pd.DataFrame, timeframe: str
) -> TechnicalSnapshot:
    frame = _series(candles)
    close = frame["close"]
    values: dict[str, Any] = {
        "sma": {"fast": sma(close, 20).iloc[-1], "slow": sma(close, 50).iloc[-1]},
        "ema": {"fast": ema(close, 12).iloc[-1], "slow": ema(close, 26).iloc[-1]},
        "macd": macd(close).iloc[-1].to_dict(),
        "rsi": {"value": rsi(close).iloc[-1]},
        "stochastic": stochastic(frame["high"], frame["low"], close).iloc[-1].to_dict(),
        "bollinger": bollinger_bands(close).iloc[-1].to_dict(),
        "atr": {"value": atr(frame).iloc[-1]},
        "adx": adx(frame).iloc[-1].to_dict(),
        "supertrend": supertrend(frame).iloc[-1].to_dict(),
        "vwap": {"value": vwap(frame).iloc[-1]},
        "obv": {"value": obv(frame).iloc[-1]},
        "cci": {"value": cci(frame).iloc[-1]},
        "roc": {"value": roc(close).iloc[-1]},
        "donchian": donchian(frame).iloc[-1].to_dict(),
        "keltner": keltner(frame).iloc[-1].to_dict(),
    }
    clean_values = {
        name: {key: _clean(value) for key, value in result.items()}
        for name, result in values.items()
    }
    signals = {
        "ema_alignment": _compare(clean_values["ema"]["fast"], clean_values["ema"]["slow"]),
        "macd": _compare(clean_values["macd"]["macd"], clean_values["macd"]["signal"]),
        "rsi": _rsi_signal(clean_values["rsi"]["value"]),
        "supertrend": (
            "bullish"
            if clean_values["supertrend"]["direction"] == 1
            else "bearish"
            if clean_values["supertrend"]["direction"] == -1
            else "neutral"
        ),
    }
    return TechnicalSnapshot(timeframe=timeframe, values=clean_values, signals=signals)


def compute_multi_timeframe(
    data: dict[str, Sequence[OHLCVBar] | pd.DataFrame],
) -> dict[str, TechnicalSnapshot]:
    return {timeframe: compute_indicators(candles, timeframe) for timeframe, candles in data.items()}


def _clean(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if np.isfinite(number) else None


def _compare(left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return "unavailable"
    return "bullish" if left > right else "bearish" if left < right else "neutral"


def _rsi_signal(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return "oversold" if value < 30 else "overbought" if value > 70 else "neutral"
