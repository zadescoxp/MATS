"""Rebuild frontend/lib/coin-logos.json: a logo URL for every coin Bybit lists.

Run when Bybit lists new coins:

    uv run python scripts/update_coin_logos.py

Logos come from CoinGecko's market listing (the largest coin wins a shared ticker), then
ticker-named icon sets for the few CoinGecko lacks. Each fallback URL is checked before it
is kept. Tokenized stocks, ETFs, and forex pairs usually have no crypto logo and keep the
console's letter mark.
"""

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from framework.config.settings import Settings
from framework.tools.market_data import MarketDataTools
from framework.tools.markets import tradable_markets

OUTPUT = Path(__file__).resolve().parents[1] / "frontend" / "lib" / "coin-logos.json"
GECKO_PAGES = 16  # 4,000 coins by market cap
# Bybit tickers whose coin trades under another symbol elsewhere.
ALIASES = {
    "RAYDIUM": "RAY",
    "SOLAYER": "LAYER",
    "LUNA2": "LUNA",
    "PUMPFUN": "PUMP",
    "SHIB1000": "SHIB",
}
FALLBACKS = (
    "https://assets.coincap.io/assets/icons/{ticker}@2x.png",
    "https://cdn.jsdelivr.net/gh/vadimmalykhin/binance-icons/crypto/{ticker}.svg",
)


def ticker(base: str) -> str:
    """1000PEPE, 1000000BABYDOGE, and SHIB1000 are the same coins as PEPE, BABYDOGE, SHIB."""
    base = ALIASES.get(base, base)
    return re.sub(r"^(10+)(?=[A-Z])|(?<=[A-Z])(10+)$", "", base)


def gecko_images() -> dict[str, str]:
    images: dict[str, str] = {}
    for page in range(1, GECKO_PAGES + 1):
        url = (
            "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
            f"&order=market_cap_desc&per_page=250&page={page}"
        )
        for _attempt in range(4):
            try:
                with urllib.request.urlopen(_request(url), timeout=20) as response:
                    rows = json.load(response)
                break
            except Exception as exc:  # rate limited; wait and retry
                print(f"page {page}: {exc}; retrying")
                time.sleep(20)
        else:
            rows = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            image = str(row.get("image") or "")
            if symbol and image.startswith("https://coin-images.coingecko.com/"):
                images.setdefault(symbol, image)  # first seen is the largest market cap
        time.sleep(7)  # CoinGecko's free API allows a few requests a minute
    return images


def fallback(base: str) -> str | None:
    for pattern in FALLBACKS:
        url = pattern.format(ticker=ticker(base).lower())
        try:
            request = _request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status == 200:
                    return url
        except Exception:
            continue
    return None


def main() -> None:
    bases = sorted({market["base"] for market in tradable_markets(_exchange())})
    images = gecko_images()
    logos = {base: images[ticker(base)] for base in bases if ticker(base) in images}
    missing = [base for base in bases if base not in logos]
    with ThreadPoolExecutor(16) as pool:
        for base, url in zip(missing, pool.map(fallback, missing), strict=True):
            if url:
                logos[base] = url
    OUTPUT.write_text(json.dumps(dict(sorted(logos.items())), indent=0) + "\n")
    print(f"Wrote {len(logos)} of {len(bases)} Bybit coins to {OUTPUT}.")


def _exchange():
    return MarketDataTools._make_exchange(Settings())


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(
        url, method=method, headers={"User-Agent": "MATS logo map", "Accept": "*/*"}
    )


if __name__ == "__main__":
    main()
