"""External context grouped by how long it should influence a trade."""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

_USER_AGENT = "MATS research context feed"
logger = logging.getLogger(__name__)
_SOURCE_TIMEOUT_SECONDS = 4.0
_COLLECT_TIMEOUT_SECONDS = 6.0
_MAX_FEED_BYTES = 2_000_000
_NEWS_MAX_AGE = timedelta(hours=24)
_SENTIMENT_CACHE_SECONDS = 300
# Keyless public feeds; the CryptoCompare news API now requires an API key.
_NEWS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
}
_GOOGLE_NEWS = "https://news.google.com/rss/search?q={query}+when:1d&hl=en-US&gl=US&ceid=US:en"
_COIN_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
}
PAIR_REFERENCES = {
    "BTC/USDT": "ETH/USDT",
    "ETH/USDT": "BTC/USDT",
    "SOL/USDT": "ETH/USDT",
    "BNB/USDT": "BTC/USDT",
    "XRP/USDT": "BTC/USDT",
    "DOGE/USDT": "BTC/USDT",
}
_LONG_HINTS = (
    "10-k",
    "10-q",
    "guidance",
    "regulation",
    "adoption",
    "halving",
    "etf",
    "treasury",
    "outlook",
    "fundamental",
)
_SHORT_HINTS = (
    "breaking",
    "today",
    "hack",
    "liquidation",
    "cpi",
    "fomc",
    "spike",
    "halt",
    "exploit",
    "outage",
)


_POSITIVE_WORDS = (
    "surge",
    "soar",
    "rally",
    "record high",
    "all-time high",
    "breakout",
    "approval",
    "approved",
    "inflows",
    "adoption",
    "upgrade",
    "bullish",
    "gains",
    "jumps",
    "partnership",
    "launches",
)
_NEGATIVE_WORDS = (
    "plunge",
    "crash",
    "slump",
    "sell-off",
    "selloff",
    "outflows",
    "lawsuit",
    "sued",
    "charges",
    "probe",
    "bearish",
    "drops",
    "falls",
    "tumbles",
    "liquidation",
    "fraud",
    "fine",
    "warning",
    "exits",
)
# Events that can invalidate a technical setup within hours.
_SHOCK_WORDS = (
    "hack",
    "exploit",
    "drained",
    "stolen",
    "heist",
    "delist",
    "bankrupt",
    "insolven",
    "halts withdrawals",
    "suspends withdrawals",
    "depeg",
    "ban ",
    "banned",
    "emergency",
    "rug pull",
)
_sentiment_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_sentiment_lock = threading.Lock()


def headline_tone(title: str) -> float:
    """Score a headline from -1 (bearish) to 1 (bullish) with a transparent word list."""
    text = title.lower()
    positive = sum(word in text for word in _POSITIVE_WORDS)
    negative = sum(word in text for word in _NEGATIVE_WORDS)
    negative += 2 * sum(word in text for word in _SHOCK_WORDS)
    total = positive + negative
    return 0.0 if total == 0 else (positive - negative) / total


def is_shock(title: str) -> bool:
    text = f"{title.lower()} "
    return any(word in text for word in _SHOCK_WORDS)


def reference_pair(symbol: str) -> str | None:
    return PAIR_REFERENCES.get(symbol.upper())


def classify_horizon(title: str, source: str) -> str:
    """Map a context item to the holding horizon it should influence."""
    text = title.lower()
    if source in {"filing", "fundamental", "gold"}:
        return "long"
    if source in {"vix", "crude", "sentiment"}:
        return "short"
    if any(hint in text for hint in _LONG_HINTS):
        return "long"
    if any(hint in text for hint in _SHORT_HINTS):
        return "short"
    return "long" if source == "news" and "sec" in text else "short"


def collect_context(symbol: str) -> dict[str, Any]:
    started_at = time.perf_counter()
    tasks = {
        "sentiment": lambda: _sentiment(),
        "news": lambda: _news(symbol),
        "macro": lambda: _macro(),
        "filings": lambda: _filings(symbol),
        "fundamentals": lambda: _fundamentals(symbol),
    }
    items: list[dict[str, str]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="mats-context") as pool:
        futures = {pool.submit(task): name for name, task in tasks.items()}
        done, pending = wait(futures, timeout=_COLLECT_TIMEOUT_SECONDS)
        for future in pending:
            future.cancel()
            errors.append(futures[future])
        for future in done:
            name = futures[future]
            try:
                items.extend(future.result())
            except Exception:
                errors.append(name)
                logger.warning("External context source failed (source=%s).", name, exc_info=True)
    short_horizon = [item for item in items if item["horizon"] == "short"]
    long_horizon = [item for item in items if item["horizon"] == "long"]
    result = {
        "items": items,
        "short_horizon": short_horizon[:8],
        "long_horizon": long_horizon[:8],
        "news_sentiment": summarize_sentiment(symbol, items, errors),
        "unavailable_sources": errors,
    }
    if errors:
        logger.warning("External context unavailable (sources=%s).", ", ".join(errors))
    logger.info(
        "External context collected in %.2fs (items=%d, unavailable=%d).",
        time.perf_counter() - started_at,
        len(items),
        len(errors),
    )
    return result


def news_sentiment(symbol: str, *, max_age_seconds: float = _SENTIMENT_CACHE_SECONDS) -> dict:
    """Fresh headline and crowd sentiment for live trading, cached briefly per symbol."""
    key = symbol.upper()
    now = time.monotonic()
    with _sentiment_lock:
        cached = _sentiment_cache.get(key)
        if cached and now - cached[0] < max_age_seconds:
            return cached[1]
    tasks = {"sentiment": _sentiment, "news": lambda: _news(symbol)}
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="mats-news") as pool:
        futures = {pool.submit(task): name for name, task in tasks.items()}
        done, pending = wait(futures, timeout=_COLLECT_TIMEOUT_SECONDS)
        for future in pending:
            future.cancel()
            errors.append(futures[future])
        for future in done:
            try:
                items.extend(future.result())
            except Exception:
                errors.append(futures[future])
                logger.warning(
                    "Live sentiment source failed (source=%s).", futures[future], exc_info=True
                )
    snapshot = summarize_sentiment(symbol, items, errors)
    with _sentiment_lock:
        _sentiment_cache[key] = (now, snapshot)
    return snapshot


def summarize_sentiment(
    symbol: str, items: list[dict[str, Any]], unavailable: list[str]
) -> dict[str, Any]:
    headlines = [item for item in items if item.get("source") == "news"]
    tones = [float(item.get("tone", 0.0)) for item in headlines]
    fear_greed = next(
        (item.get("value") for item in items if item.get("source") == "sentiment"), None
    )
    return {
        "symbol": symbol,
        "fetched_at": datetime.now(UTC).isoformat(),
        "headline_count": len(headlines),
        "news_score": round(sum(tones) / len(tones), 3) if tones else None,
        "fear_greed": fear_greed,
        "shocks": [
            {key: item.get(key) for key in ("title", "publisher", "published", "coin_specific")}
            for item in headlines
            if item.get("shock")
        ],
        "headlines": [
            {
                key: item.get(key)
                for key in ("title", "publisher", "published", "tone", "coin_specific")
            }
            for item in headlines[:10]
        ],
        "unavailable_sources": [name for name in unavailable if name in {"news", "sentiment"}],
    }


def _sentiment() -> list[dict[str, Any]]:
    payload = _get_json("https://api.alternative.me/fng/?limit=1")
    row = payload["data"][0]
    value = row.get("value")
    label = row.get("value_classification", "unknown")
    item: dict[str, Any] = _item(
        "sentiment",
        f"Crypto fear and greed is {value} ({label})",
        "Public sentiment index. Use it as a short-horizon crowding check, not a thesis.",
    )
    try:
        item["value"] = int(value)
    except (TypeError, ValueError):
        item["value"] = None
    return [item]


def _news(symbol: str) -> list[dict[str, Any]]:
    base = symbol.split("/")[0].upper()
    name = _COIN_IDS.get(base, "").replace("binancecoin", "bnb").replace("ripple", "xrp")
    terms = {base.lower(), *([name] if name else [])}
    query = urllib.request.quote(f"{name or base.lower()} crypto")
    feeds = {**_NEWS_FEEDS, "Google News": _GOOGLE_NEWS.format(query=query)}
    cutoff = datetime.now(UTC) - _NEWS_MAX_AGE
    seen: set[str] = set()
    stories: list[dict[str, Any]] = []
    failures = 0
    for publisher, url in feeds.items():
        try:
            entries = _rss_items(url)
        except Exception:
            failures += 1
            logger.warning("News feed failed (publisher=%s).", publisher, exc_info=True)
            continue
        for title, published, source in entries:
            if published is not None and published < cutoff:
                continue
            words = set(re.findall(r"[a-z0-9]+", title.lower()))
            coin_specific = bool(terms & words)
            if publisher == "Google News":
                coin_specific = True
            elif not coin_specific and base not in {"BTC", "ETH"}:
                # General crypto feeds are market-wide context for majors only.
                if not is_shock(title):
                    continue
            key = title.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            story = _item("news", title, f"{source or publisher} headline.")
            story.update(
                publisher=source or publisher,
                published=published.isoformat() if published else None,
                tone=round(headline_tone(title), 3),
                shock=is_shock(title),
                coin_specific=coin_specific,
            )
            stories.append(story)
    if failures == len(feeds):
        raise RuntimeError("All news feeds failed")
    stories.sort(key=lambda item: item.get("published") or "", reverse=True)
    return stories[:15]


def _rss_items(url: str) -> list[tuple[str, datetime | None, str | None]]:
    root = ET.fromstring(_get_bytes(url))
    entries = []
    for node in root.findall("./channel/item"):
        title = re.sub(r"\s+", " ", node.findtext("title") or "").strip()
        if not title:
            continue
        source = node.findtext("source")
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(node.findtext("pubDate") or "")
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            published = None
        entries.append((title, published, source))
    return entries


def _macro() -> list[dict[str, str]]:
    series = {
        "^VIX": ("vix", "VIX"),
        "GC=F": ("gold", "Gold"),
        "CL=F": ("crude", "Crude"),
    }
    items = []
    for ticker, (source, label) in series.items():
        chart = _get_json(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(ticker)}"
            "?interval=1d&range=5d"
        )
        result = chart["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        closes = [value for value in closes if isinstance(value, int | float)]
        if len(closes) < 2:
            continue
        change = (closes[-1] - closes[-2]) / closes[-2] if closes[-2] else 0
        items.append(
            _item(
                source,
                f"{label} last {closes[-1]:.2f} ({change:+.2%} on the day)",
                f"{label} is macro context. Gold is slower than VIX or crude.",
            )
        )
    return items


def _filings(symbol: str) -> list[dict[str, str]]:
    query = urllib.request.quote(symbol.split("/")[0].lower())
    payload = _get_json(
        "https://efts.sec.gov/LATEST/search-index?"
        f"q={query}&forms=8-K&dateRange=custom&startdt=2024-01-01"
    )
    hits = payload.get("hits", {}).get("hits", [])[:3]
    items = []
    for hit in hits:
        source = hit.get("_source", {})
        title = source.get("display_names") or source.get("file_description") or "SEC filing"
        if isinstance(title, list):
            title = title[0] if title else "SEC filing"
        form = source.get("form") or "filing"
        items.append(
            _item(
                "filing",
                f"SEC {form}: {title}",
                "Issuer filing. Treat it as long-horizon context unless it is a same-day shock.",
            )
        )
    return items


def _fundamentals(symbol: str) -> list[dict[str, str]]:
    coin = _COIN_IDS.get(symbol.split("/")[0].upper())
    if coin is None:
        return []
    payload = _get_json(
        f"https://api.coingecko.com/api/v3/coins/{coin}"
        "?localization=false&tickers=false&market_data=true&community_data=false"
        "&developer_data=false&sparkline=false"
    )
    market = payload.get("market_data") or {}
    cap = (market.get("market_cap") or {}).get("usd")
    rank = market.get("market_cap_rank")
    if cap is None:
        return []
    return [
        _item(
            "fundamental",
            f"{payload.get('name', coin)} market cap ${cap:,.0f}, rank {rank}",
            "Slow-moving size and rank. Use it for longer-horizon bias, not an intraday entry.",
        )
    ]


def _item(source: str, title: str, summary: str) -> dict[str, Any]:
    return {
        "source": source,
        "title": title,
        "summary": summary,
        "horizon": classify_horizon(title, source),
    }


def _get_json(url: str) -> Any:
    return json.loads(_get_bytes(url).decode())


def _get_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json, application/xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_SOURCE_TIMEOUT_SECONDS) as response:
            return response.read(_MAX_FEED_BYTES)
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc
