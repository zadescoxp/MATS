"""Deterministic news and sentiment gate for a running strategy algorithm."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

SHOCK_WINDOW = timedelta(hours=6)
BEARISH_NEWS_SCORE = -0.35
MIN_HEADLINES_FOR_TONE = 5
EXTREME_GREED = 90


@dataclass(frozen=True)
class NewsRisk:
    block_entry: bool
    force_exit: bool
    reason: str


def assess_news_risk(
    snapshot: dict[str, Any] | None, side: str = "long", *, now: datetime | None = None
) -> NewsRisk:
    """Decide whether fresh news should stop a new entry or close an open position.

    Coin-specific shocks (hacks, delistings, halted withdrawals) close longs and block
    entries. Market-wide shocks and broadly bearish headlines only block new longs.
    Missing news never blocks trading; it is reported so the operator can see it.
    """
    if not snapshot or (not snapshot.get("headline_count") and snapshot.get("fear_greed") is None):
        return NewsRisk(False, False, "News and sentiment unavailable; trading on price only.")
    if side != "long":
        return NewsRisk(False, False, "News gate only applies to long spot positions.")
    current = now or datetime.now(UTC)
    recent_shocks = [shock for shock in snapshot.get("shocks") or [] if _is_recent(shock, current)]
    coin_shock = next((shock for shock in recent_shocks if shock.get("coin_specific")), None)
    if coin_shock:
        return NewsRisk(True, True, f"Coin-specific shock headline: {coin_shock.get('title')}")
    if recent_shocks:
        return NewsRisk(True, False, f"Market-wide shock headline: {recent_shocks[0].get('title')}")
    score = snapshot.get("news_score")
    count = int(snapshot.get("headline_count") or 0)
    fear_greed = snapshot.get("fear_greed")
    summary = _summary(score, count, fear_greed)
    if score is not None and count >= MIN_HEADLINES_FOR_TONE and score <= BEARISH_NEWS_SCORE:
        return NewsRisk(True, False, f"Headline tone is bearish; standing aside. {summary}")
    if isinstance(fear_greed, int) and fear_greed >= EXTREME_GREED:
        return NewsRisk(True, False, f"Extreme greed suggests a crowded long. {summary}")
    return NewsRisk(False, False, f"News clear. {summary}")


def _is_recent(shock: dict[str, Any], now: datetime) -> bool:
    published = shock.get("published")
    if not published:
        return False
    try:
        timestamp = datetime.fromisoformat(str(published))
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return now - timestamp <= SHOCK_WINDOW


def _summary(score: Any, count: int, fear_greed: Any) -> str:
    tone = "n/a" if score is None else f"{float(score):+.2f}"
    crowd = "n/a" if fear_greed is None else str(fear_greed)
    return f"Tone {tone} over {count} headlines; fear and greed {crowd}."
