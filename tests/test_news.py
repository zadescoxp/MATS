from datetime import UTC, datetime, timedelta

from framework.strategies.news_risk import assess_news_risk
from framework.tools.context_feed import headline_tone, is_shock, summarize_sentiment

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


def _snapshot(**overrides):
    snapshot = {
        "headline_count": 6,
        "news_score": 0.1,
        "fear_greed": 50,
        "shocks": [],
    }
    snapshot.update(overrides)
    return snapshot


def test_headline_tone_and_shock_detection() -> None:
    assert headline_tone("Bitcoin price surges to record high") > 0
    assert headline_tone("Exchange hack drains $40M as tokens plunge") < 0
    assert headline_tone("Weekly market recap") == 0
    assert is_shock("Exchange halts withdrawals after exploit")
    assert not is_shock("Analysts expect calmer bitcoin markets")


def test_summarize_sentiment_aggregates_headlines_and_fear_greed() -> None:
    items = [
        {"source": "sentiment", "value": 22},
        {"source": "news", "title": "BTC rally", "tone": 1.0, "shock": False},
        {"source": "news", "title": "BTC hack", "tone": -1.0, "shock": True},
    ]
    summary = summarize_sentiment("BTC/USDT", items, ["filings"])
    assert summary["headline_count"] == 2
    assert summary["news_score"] == 0
    assert summary["fear_greed"] == 22
    assert [shock["title"] for shock in summary["shocks"]] == ["BTC hack"]
    assert summary["unavailable_sources"] == []


def test_missing_news_never_blocks_trading() -> None:
    risk = assess_news_risk(None, now=NOW)
    assert not risk.block_entry and not risk.force_exit
    assert "unavailable" in risk.reason


def test_recent_coin_shock_blocks_entry_and_forces_exit() -> None:
    shock = {
        "title": "BTC custodian hacked",
        "published": (NOW - timedelta(hours=1)).isoformat(),
        "coin_specific": True,
    }
    risk = assess_news_risk(_snapshot(shocks=[shock]), now=NOW)
    assert risk.block_entry and risk.force_exit


def test_market_wide_shock_blocks_only_new_entries() -> None:
    shock = {
        "title": "Major exchange halts withdrawals",
        "published": (NOW - timedelta(hours=2)).isoformat(),
        "coin_specific": False,
    }
    risk = assess_news_risk(_snapshot(shocks=[shock]), now=NOW)
    assert risk.block_entry and not risk.force_exit


def test_old_shock_is_ignored() -> None:
    shock = {
        "title": "BTC custodian hacked",
        "published": (NOW - timedelta(hours=30)).isoformat(),
        "coin_specific": True,
    }
    risk = assess_news_risk(_snapshot(shocks=[shock]), now=NOW)
    assert not risk.block_entry and not risk.force_exit


def test_bearish_tone_and_extreme_greed_block_new_longs() -> None:
    assert assess_news_risk(_snapshot(news_score=-0.5), now=NOW).block_entry
    assert not assess_news_risk(_snapshot(news_score=-0.5, headline_count=2), now=NOW).block_entry
    assert assess_news_risk(_snapshot(fear_greed=95), now=NOW).block_entry
    assert not assess_news_risk(_snapshot(), now=NOW).block_entry
