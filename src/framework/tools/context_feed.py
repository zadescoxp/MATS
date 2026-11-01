"""External context grouped by how long it should influence a trade."""

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any

_USER_AGENT = "MATS research context feed"
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
        done, pending = wait(futures, timeout=2.5)
        for future in pending:
            future.cancel()
            errors.append(futures[future])
        for future in done:
            name = futures[future]
            try:
                items.extend(future.result())
            except Exception:
                errors.append(name)
    short_horizon = [item for item in items if item["horizon"] == "short"]
    long_horizon = [item for item in items if item["horizon"] == "long"]
    return {
        "items": items,
        "short_horizon": short_horizon[:8],
        "long_horizon": long_horizon[:8],
        "unavailable_sources": errors,
    }


def _sentiment() -> list[dict[str, str]]:
    payload = _get_json("https://api.alternative.me/fng/?limit=1")
    row = payload["data"][0]
    value = row.get("value")
    label = row.get("value_classification", "unknown")
    return [
        _item(
            "sentiment",
            f"Crypto fear and greed is {value} ({label})",
            "Public sentiment index. Use it as a short-horizon crowding check, not a thesis.",
        )
    ]


def _news(symbol: str) -> list[dict[str, str]]:
    base = symbol.split("/")[0]
    payload = _get_json("https://min-api.cryptocompare.com/data/v2/news/?lang=EN&extraParams=mats")
    stories = []
    for article in payload.get("Data", [])[:12]:
        title = str(article.get("title") or "").strip()
        if not title:
            continue
        categories = str(article.get("categories") or "")
        if base not in title.upper() and base not in categories.upper() and "BTC" not in categories:
            if base not in {"BTC", "ETH"}:
                continue
        body = str(article.get("body") or "")[:240]
        stories.append(_item("news", title, body or "Headline only."))
        if len(stories) == 5:
            break
    return stories


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


def _item(source: str, title: str, summary: str) -> dict[str, str]:
    return {
        "source": source,
        "title": title,
        "summary": summary,
        "horizon": classify_horizon(title, source),
    }


def _get_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return json.loads(response.read().decode())
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc
