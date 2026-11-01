from datetime import UTC, datetime, timedelta

from framework.schemas.models import OHLCVBar
from framework.strategies.algorithm import bar_already_evaluated
from framework.strategies.regime import diagnose_regime
from framework.tools.context_feed import classify_horizon


def test_trending_series_is_not_labeled_choppy() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    candles = [
        OHLCVBar(
            timestamp=start + timedelta(hours=index),
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100 + index,
            volume=10,
        )
        for index in range(80)
    ]
    regime = diagnose_regime({"1h": candles})
    assert regime["regime"].startswith("trending")
    assert regime["trend_strength"] > 0.25
    assert regime["higher_tf_bias"] == "bullish"
    assert "avoid" in regime["recommendation"] or "prefer" in regime["recommendation"]


def test_algorithm_ignores_a_bar_it_already_evaluated() -> None:
    assert bar_already_evaluated("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")
    assert not bar_already_evaluated(None, "2026-01-01T00:00:00+00:00")
    assert not bar_already_evaluated("2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00")


def test_context_horizon_separates_slow_and_fast_inputs() -> None:
    assert classify_horizon("SEC 10-K bitcoin treasury outlook", "filing") == "long"
    assert classify_horizon("Gold last 2300", "gold") == "long"
    assert classify_horizon("VIX last 18", "vix") == "short"
    assert classify_horizon("Breaking liquidation spike", "news") == "short"
    assert classify_horizon("Spot ETF adoption guidance", "news") == "long"
