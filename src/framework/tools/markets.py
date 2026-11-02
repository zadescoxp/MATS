"""Which Bybit market a symbol names, and the list of markets a run may trade.

Symbols use ccxt's unified form: ``BTC/USDT`` is the spot market and ``BTC/USDT:USDT``
the USDT-settled linear perpetual. The suffix after ``:`` is the settlement coin, so the
market type travels with the symbol through runs, the candle cache, and the database.
"""

import logging
import threading
import time
from typing import Any, Literal

logger = logging.getLogger(__name__)

MarketType = Literal["spot", "perpetual"]

# Quote currencies runs may trade in; balances, sizing, and fees are kept in these.
QUOTE_CURRENCIES = ("USDT", "USDC")
_LISTING_TTL_SECONDS = 3_600


def is_perpetual(symbol: str) -> bool:
    return ":" in symbol


def market_type(symbol: str) -> MarketType:
    return "perpetual" if is_perpetual(symbol) else "spot"


def base_currency(symbol: str) -> str:
    return symbol.split("/")[0]


def quote_currency(symbol: str) -> str:
    """The coin prices are quoted in; for a linear perpetual it is also the margin coin."""
    return symbol.split("/")[-1].split(":")[0]


def spot_symbol(symbol: str) -> str:
    return symbol.split(":")[0]


def perpetual_symbol(symbol: str) -> str:
    spot = spot_symbol(symbol)
    return f"{spot}:{quote_currency(spot)}"


def same_market(symbol: str, like: str) -> str:
    """``symbol`` on the same market type as ``like`` (spot or perpetual)."""
    return perpetual_symbol(symbol) if is_perpetual(like) else spot_symbol(symbol)


def tradable_markets(exchange: Any) -> list[dict[str, str]]:
    """Active Bybit spot pairs and linear perpetuals quoted in a supported currency."""
    markets: list[dict[str, str]] = []
    for market in exchange.load_markets().values():
        if not market.get("active", True) or market.get("quote") not in QUOTE_CURRENCIES:
            continue
        if market.get("spot"):
            kind: MarketType = "spot"
        elif (
            market.get("swap")
            and market.get("linear")
            and market.get("settle") == market.get("quote")
        ):
            kind = "perpetual"
        else:
            continue
        markets.append(
            {
                "symbol": str(market["symbol"]),
                "base": str(market["base"]),
                "quote": str(market["quote"]),
                "type": kind,
            }
        )
    markets.sort(key=lambda item: (item["type"], item["base"], item["quote"]))
    return markets


class MarketListing:
    """Bybit's tradable markets, loaded once an hour and shared by every request."""

    def __init__(self, exchange_factory: Any, ttl_seconds: float = _LISTING_TTL_SECONDS):
        self._exchange_factory = exchange_factory
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._markets: list[dict[str, str]] | None = None
        self._symbols: frozenset[str] = frozenset()
        self._loaded_at = 0.0

    def markets(self) -> list[dict[str, str]]:
        with self._lock:
            if self._markets is None or time.monotonic() - self._loaded_at > self._ttl:
                markets = tradable_markets(self._exchange_factory())
                self._markets = markets
                self._symbols = frozenset(item["symbol"] for item in markets)
                self._loaded_at = time.monotonic()
                logger.info("Loaded Bybit market listing (markets=%d).", len(markets))
            return self._markets

    def contains(self, symbol: str) -> bool:
        self.markets()
        return symbol in self._symbols

    def known(self, symbol: str) -> bool | None:
        """Whether the last loaded listing has ``symbol``; None before any listing loaded."""
        with self._lock:
            return None if self._markets is None else symbol in self._symbols
