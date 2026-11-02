"""A read-only view of the connected Bybit account: what it holds and what is free."""

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from framework.schemas.models import AccountBalance

logger = logging.getLogger(__name__)


class AssetHolding(BaseModel):
    asset: str
    total: float
    free: float
    locked: float
    usd_value: float | None = None
    borrowed: float = 0.0


class AccountSnapshot(BaseModel):
    # Which venue the balances come from; each connected venue gets its own snapshot.
    venue: str = "bybit"
    venue_name: str = "Bybit"
    source: str = "Bybit API · Unified account wallet balance"
    fetched_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    connected: bool = True
    testnet: bool = True
    account_type: str | None = None
    total_equity_usd: float | None = None
    available_usd: float | None = None
    assets: list[AssetHolding] = Field(default_factory=list)
    error: str | None = None


def account_snapshot(balance: AccountBalance, *, testnet: bool) -> AccountSnapshot:
    """Holdings with a non-zero balance, largest USD value first."""
    account = _unified_account(balance.info)
    coins = {str(coin.get("coin")): coin for coin in account.get("coin") or []}
    holdings: list[AssetHolding] = []
    for asset, total in balance.total.items():
        amount = _number(total)
        if not amount:
            continue
        coin = coins.get(asset, {})
        holdings.append(
            AssetHolding(
                asset=asset,
                total=amount,
                free=balance.available(asset),
                locked=_number(balance.used.get(asset)) or 0.0,
                usd_value=_number(coin.get("usdValue")),
                borrowed=_number(coin.get("borrowAmount")) or 0.0,
            )
        )
    holdings.sort(key=lambda item: item.usd_value or 0.0, reverse=True)
    return AccountSnapshot(
        testnet=testnet,
        account_type=account.get("accountType"),
        total_equity_usd=_number(account.get("totalEquity")),
        available_usd=_number(account.get("totalAvailableBalance")),
        assets=holdings,
    )


def coin_detail(balance: AccountBalance, coin: str, field: str) -> float:
    """A per-coin number from Bybit's Unified account payload, e.g. borrowAmount."""
    for item in _unified_account(balance.info).get("coin") or []:
        if item.get("coin") == coin:
            return max(_number(item.get(field)) or 0.0, 0.0)
    return 0.0


def _unified_account(info: dict[str, Any]) -> dict[str, Any]:
    accounts = (info.get("result") or {}).get("list") or []
    return accounts[0] if accounts and isinstance(accounts[0], dict) else {}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN
