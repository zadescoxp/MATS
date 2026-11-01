"""Deterministic market-state features for strategy selection."""

from typing import Any

import numpy as np
import pandas as pd

from framework.schemas.models import OHLCVBar
from framework.tools.technicals import adx, atr, ema


def diagnose_regime(
    raw_data: dict[str, list[OHLCVBar]],
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not raw_data:
        raise ValueError("Regime diagnosis requires OHLCV data")
    ordered = sorted(raw_data, key=_timeframe_seconds)
    primary = raw_data[ordered[-1]]
    if len(primary) < 30:
        shortest = max(raw_data, key=lambda timeframe: len(raw_data[timeframe]))
        primary = raw_data[shortest]
        ordered_primary = shortest
    else:
        ordered_primary = ordered[-1]
    frame = _frame(primary)
    close = frame["close"]
    trend_strength = _trend_strength(frame)
    vol_percentile = _atr_percentile(frame)
    autocorr = _return_autocorr(close)
    hurst = _hurst(close)
    higher_tf_bias = _higher_timeframe_bias(frame)
    if trend_strength >= 0.25:
        trend = "trending"
    else:
        trend = "choppy"
    if vol_percentile >= 67:
        vol = "high_vol"
    elif vol_percentile <= 33:
        vol = "low_vol"
    else:
        vol = "normal_vol"
    regime = f"{trend}_{vol}"
    recommendation = _recommendation(trend, vol, autocorr)
    external = (market_context or {}).get("external") or {}
    long_horizon = [
        item.get("title") for item in external.get("long_horizon", []) if item.get("title")
    ]
    short_horizon = [
        item.get("title") for item in external.get("short_horizon", []) if item.get("title")
    ]
    if long_horizon:
        recommendation += " Long-horizon context should set the directional bias."
    if short_horizon:
        recommendation += " Short-horizon context should only change timing or stand aside."
    return {
        "regime": regime,
        "timeframe": ordered_primary,
        "trend_strength": round(trend_strength, 4),
        "vol_percentile": round(vol_percentile, 1),
        "return_autocorr": None if autocorr is None else round(autocorr, 4),
        "hurst": None if hurst is None else round(hurst, 4),
        "higher_tf_bias": higher_tf_bias,
        "mean_reversion_tendency": _reversion_label(autocorr, hurst),
        "recommendation": recommendation,
        "long_horizon_context": long_horizon[:6],
        "short_horizon_context": short_horizon[:6],
    }


def _frame(candles: list[OHLCVBar]) -> pd.DataFrame:
    frame = pd.DataFrame([bar.model_dump() for bar in candles])
    return frame.astype({name: "float64" for name in ("open", "high", "low", "close", "volume")})


def _trend_strength(frame: pd.DataFrame) -> float:
    strength = adx(frame).iloc[-1]["adx"]
    adx_strength = float(strength) / 100 if pd.notna(strength) else 0.0
    efficiency = _efficiency_ratio(frame["close"], 20)
    return float(np.clip(max(adx_strength, efficiency), 0.0, 1.0))


def _efficiency_ratio(close: pd.Series, period: int) -> float:
    if len(close) <= period:
        return 0.0
    change = abs(float(close.iloc[-1] - close.iloc[-period - 1]))
    path = float(close.diff().abs().iloc[-period:].sum())
    if path <= 0:
        return 0.0
    return change / path


def _atr_percentile(frame: pd.DataFrame, period: int = 14, lookback: int = 100) -> float:
    series = (atr(frame, period) / frame["close"]).dropna()
    if series.empty:
        return 50.0
    window = series.iloc[-lookback:]
    latest = float(window.iloc[-1])
    return float((window <= latest).mean() * 100)


def _return_autocorr(close: pd.Series) -> float | None:
    returns = close.pct_change().dropna()
    if len(returns) < 20:
        return None
    value = returns.autocorr(lag=1)
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def _hurst(close: pd.Series) -> float | None:
    """Variance-ratio estimate. Below 0.5 leans mean-reverting, above 0.5 trending."""
    returns = close.pct_change().dropna().to_numpy()
    if len(returns) < 40:
        return None
    lag = 4
    first = returns[:-lag]
    aggregated = np.lib.stride_tricks.sliding_window_view(returns, lag).sum(axis=1)
    var_one = float(np.var(first))
    var_lag = float(np.var(aggregated))
    if var_one <= 0 or var_lag <= 0:
        return None
    ratio = var_lag / (lag * var_one)
    return float(np.clip(0.5 * np.log(ratio) / np.log(lag) + 0.5, 0.0, 1.0))


def _higher_timeframe_bias(frame: pd.DataFrame) -> str:
    close = frame["close"]
    fast = ema(close, 50).iloc[-1] if len(close) >= 50 else np.nan
    slow = ema(close, 200).iloc[-1] if len(close) >= 200 else np.nan
    price = float(close.iloc[-1])
    if pd.notna(fast) and pd.notna(slow):
        if price > fast > slow:
            return "bullish"
        if price < fast < slow:
            return "bearish"
        return "neutral"
    if pd.notna(fast):
        if price > float(fast) * 1.002:
            return "bullish"
        if price < float(fast) * 0.998:
            return "bearish"
    return "neutral"


def _reversion_label(autocorr: float | None, hurst: float | None) -> str:
    if autocorr is not None and autocorr < -0.05:
        return "mean_reverting"
    if hurst is not None and hurst < 0.45:
        return "mean_reverting"
    if autocorr is not None and autocorr > 0.05:
        return "persistent"
    if hurst is not None and hurst > 0.55:
        return "persistent"
    return "neutral"


def _recommendation(trend: str, vol: str, autocorr: float | None) -> str:
    if trend == "choppy":
        return (
            "prefer mean-reversion or relative-value; avoid pure trend-following"
            if vol != "high_vol" or (autocorr is not None and autocorr < 0)
            else "prefer mean-reversion or a volatility fade; avoid a pure EMA cross"
        )
    if vol == "high_vol":
        return "prefer breakout-with-confirmation or regime-gated trend"
    return "prefer regime-gated trend or a higher-timeframe hybrid; avoid an unfiltered EMA cross"


def _timeframe_seconds(timeframe: str) -> int:
    units = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
    try:
        return int(timeframe[:-1]) * units[timeframe[-1]]
    except (KeyError, ValueError):
        return 0
