import json
from datetime import UTC, datetime, timedelta

from framework.agents.multi_timeframe_analyst import prompt_market_context
from framework.schemas.models import OHLCVBar


def _bars(start_price: float, step: float, count: int = 150) -> list[OHLCVBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = []
    for index in range(count):
        price = start_price + step * index + (index % 3)
        bars.append(
            OHLCVBar(
                timestamp=start + timedelta(hours=index),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=10,
            )
        )
    return bars


def test_prompt_context_summarizes_pair_candles_instead_of_sending_them() -> None:
    primary = _bars(100, 0.5)
    pair = [bar.model_dump(mode="json") for bar in _bars(200, 1.0)]
    market_context = {
        "ticker": {"last": 175.0},
        "pair_symbol": "BTC/USDT",
        "pair_candles": {"1h": pair},
        "funding_history": [{"rate": index} for index in range(50)],
        "external": {
            "items": [{"title": "duplicate"}] * 20,
            "short_horizon": [{"title": "CPI today"}],
            "long_horizon": [],
            "news_sentiment": {"news_score": 0.1},
        },
    }

    context = prompt_market_context(market_context, {"1h": primary})

    assert "pair_candles" not in context
    assert "items" not in context["external"]
    assert context["external"]["short_horizon"] == [{"title": "CPI today"}]
    summary = context["pair_summary"]["1h"]
    assert summary["last_close"] == pair[-1]["close"]
    assert summary["return_correlation"] is not None
    assert -1 <= summary["return_correlation"] <= 1
    assert len(context["recent_funding"]) == 5
    assert len(json.dumps(context)) < len(json.dumps(market_context)) / 10


def test_prompt_context_handles_missing_pair_history() -> None:
    context = prompt_market_context({"pair_candles": {"1h": [_bars(200, 1.0, 5)[0]]}}, {})
    summary = context["pair_summary"]["1h"]
    assert summary["return_20_bars"] is None
    assert "return_correlation" not in summary


def test_analyst_submission_accepts_bias_synonyms_and_missing_descriptions() -> None:
    import pytest
    from pydantic import ValidationError

    from framework.schemas.models import AnalystSubmission

    submission = AnalystSubmission.model_validate(
        {"higher_timeframe_bias": "Uptrend", "lower_timeframe_timing": "sideways"}
    )
    assert submission.higher_timeframe_bias == "bullish"
    assert submission.lower_timeframe_timing == "neutral"
    assert submission.volatility == "unclear"
    with pytest.raises(ValidationError):
        AnalystSubmission.model_validate(
            {"higher_timeframe_bias": "long-horizon", "lower_timeframe_timing": "neutral"}
        )
