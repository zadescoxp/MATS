from framework.tools.technicals import compute_indicators, compute_multi_timeframe


def test_indicator_suite_returns_all_requested_indicators(candles) -> None:
    snapshot = compute_indicators(candles, "1h")
    expected = {
        "sma",
        "ema",
        "macd",
        "rsi",
        "stochastic",
        "bollinger",
        "atr",
        "adx",
        "supertrend",
        "vwap",
        "obv",
        "cci",
        "roc",
        "donchian",
        "keltner",
    }
    assert set(snapshot.values) == expected
    assert set(snapshot.signals) == {"ema_alignment", "macd", "rsi", "supertrend"}
    assert snapshot.values["rsi"]["value"] is not None


def test_indicators_support_multiple_timeframes(candles) -> None:
    result = compute_multi_timeframe({"1h": candles, "4h": candles[:60]})
    assert set(result) == {"1h", "4h"}
