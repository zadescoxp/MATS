import hashlib
import time
from collections.abc import Callable
from typing import Any

import ccxt

from framework.config.settings import Settings


class ExecutionTools:
    def __init__(
        self,
        settings: Settings,
        exchange: Any | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.exchange = exchange or self._make_exchange(settings)
        self._sleep = sleep
        self._monotonic = monotonic

    @staticmethod
    def _make_exchange(settings: Settings) -> Any:
        config: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": settings.request_timeout_ms,
            "rateLimit": settings.exchange_rate_limit_ms,
        }
        if settings.bybit_api_key:
            config["apiKey"] = settings.bybit_api_key.get_secret_value()
        if settings.bybit_api_secret:
            config["secret"] = settings.bybit_api_secret.get_secret_value()
        exchange = ccxt.bybit(config)
        exchange.set_sandbox_mode(settings.bybit_testnet)
        return exchange

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float | None = None,
        *,
        dry_run: bool = True,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        if amount <= 0:
            raise ValueError("amount must be positive")
        if order_type not in {"market", "limit"}:
            raise ValueError("order_type must be 'market' or 'limit'")
        if order_type == "limit" and (price is None or price <= 0):
            raise ValueError("limit orders require a positive price")
        if dry_run:
            return {
                "status": "dry_run",
                "order": {
                    "symbol": symbol,
                    "side": side,
                    "type": order_type,
                    "amount": amount,
                    "price": price,
                    "idempotency_key": idempotency_key,
                },
                "fills": [],
                "errors": [],
            }
        if not self.settings.bybit_testnet:
            raise ValueError("Live mainnet execution is disabled; Bybit testnet is required")
        if not self.settings.bybit_api_key or not self.settings.bybit_api_secret:
            raise ValueError("Bybit testnet API credentials are required for live execution")

        link_id = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        try:
            order = self.exchange.create_order(
                symbol,
                order_type,
                side,
                amount,
                price,
                {"orderLinkId": link_id},
            )
        except ccxt.DuplicateOrderId:
            order = self.exchange.fetch_order(None, symbol, {"orderLinkId": link_id})

        orders = [order]
        current = order
        deadline = self._monotonic() + self.settings.execution_timeout_seconds
        while current.get("status") not in {"closed", "canceled", "rejected"}:
            if self._monotonic() >= deadline:
                try:
                    self.exchange.cancel_order(current["id"], symbol)
                except ccxt.OrderNotFound:
                    pass
                current = self.exchange.fetch_order(current["id"], symbol)
                break
            self._sleep(self.settings.execution_poll_interval_seconds)
            current = self.exchange.fetch_order(current["id"], symbol)
            orders.append(current)

        filled = float(current.get("filled") or 0)
        requested = float(current.get("amount") or amount)
        if current.get("status") == "closed" and filled >= requested:
            status = "filled"
        elif filled > 0:
            status = "partially_filled"
        else:
            status = "rejected" if current.get("status") == "rejected" else "failed"
        errors = (
            ["Order did not reach a full fill before timeout; remaining quantity was canceled."]
            if status in {"partially_filled", "failed"} and self._monotonic() >= deadline
            else []
        )
        return {
            "status": status,
            "orders": orders,
            "fills": current.get("trades") or [],
            "filled": filled,
            "remaining": float(current.get("remaining") or max(requested - filled, 0)),
            "errors": errors,
        }

    def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return self.exchange.cancel_order(order_id, symbol)

    def get_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return self.exchange.fetch_order(order_id, symbol)

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return self.exchange.fetch_open_orders(symbol)

    def get_balance(self) -> dict[str, Any]:
        if not self.settings.bybit_api_key or not self.settings.bybit_api_secret:
            raise ValueError("Bybit API credentials are required to fetch account balance")
        return self.exchange.fetch_balance()
