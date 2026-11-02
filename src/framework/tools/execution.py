import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any

import ccxt

from framework.config.settings import Settings
from framework.schemas.models import AccountBalance, OrderExecutionResult, OrderSnapshot
from framework.tools.markets import is_perpetual

logger = logging.getLogger(__name__)


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
        }
        if settings.exchange_rate_limit_ms is not None:
            config["rateLimit"] = settings.exchange_rate_limit_ms
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
        borrow: bool = False,
        reduce_only: bool = False,
    ) -> OrderExecutionResult:
        if side not in {"buy", "sell"}:
            raise ValueError("side must be 'buy' or 'sell'")
        if amount <= 0:
            raise ValueError("amount must be positive")
        if order_type not in {"market", "limit"}:
            raise ValueError("order_type must be 'market' or 'limit'")
        if order_type == "limit" and (price is None or price <= 0):
            raise ValueError("limit orders require a positive price")
        logger.info(
            "Order requested (symbol=%s, side=%s, type=%s, amount=%.8g, mode=%s).",
            symbol,
            side,
            order_type,
            amount,
            "dry-run" if dry_run else "testnet",
        )
        if dry_run:
            result = OrderExecutionResult(
                status="dry_run",
                orders=[
                    OrderSnapshot(
                        symbol=symbol,
                        side=side,
                        type=order_type,
                        status="dry_run",
                        amount=amount,
                        price=price,
                        raw={
                            "idempotency_key": idempotency_key,
                        },
                    )
                ],
                errors=[],
            )
            logger.info("Order simulated (status=dry_run).")
            return result
        if not self.settings.bybit_testnet:
            raise ValueError("Live mainnet execution is disabled; Bybit testnet is required")
        if not self.settings.bybit_api_key or not self.settings.bybit_api_secret:
            raise ValueError("Bybit testnet API credentials are required for live execution")

        link_id = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        params: dict[str, Any] = {"orderLinkId": link_id}
        if is_perpetual(symbol):
            if borrow:
                raise ValueError("Perpetual orders use margin; spot borrowing does not apply")
            if reduce_only:
                # An exit can only shrink the position, never flip it into the other side.
                params["reduceOnly"] = True
        else:
            if borrow:
                # Bybit Unified spot margin: borrow the shortfall of this order.
                params["isLeverage"] = 1
            if order_type == "market":
                # Bybit spot market buys default to quote-currency qty; size is in base units.
                params["marketUnit"] = "baseCoin"
        started_at = self._monotonic()
        try:
            order = self.exchange.create_order(
                symbol,
                order_type,
                side,
                amount,
                price,
                params,
            )
        except ccxt.DuplicateOrderId:
            logger.warning("Order idempotency key already exists; retrieving existing order.")
            order = self._fetch_order(None, symbol, {"orderLinkId": link_id})

        orders = [order]
        current = order
        deadline = self._monotonic() + self.settings.execution_timeout_seconds
        while current.get("status") not in {"closed", "canceled", "rejected"}:
            if self._monotonic() >= deadline:
                try:
                    self.exchange.cancel_order(current["id"], symbol)
                except ccxt.OrderNotFound:
                    pass
                current = self._fetch_order(current["id"], symbol)
                break
            self._sleep(self.settings.execution_poll_interval_seconds)
            current = self._fetch_order(current["id"], symbol)
            orders.append(current)

        filled = float(current.get("filled") or 0)
        requested = float(current.get("amount") or amount)
        if current.get("status") == "closed" and filled > 0:
            # A closed order is done; exchange lot rounding can leave filled just below amount.
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
        result = OrderExecutionResult(
            status=status,
            orders=[_normalize_order(item, symbol) for item in orders],
            fills=current.get("trades") or [],
            filled=filled,
            remaining=float(current.get("remaining") or max(requested - filled, 0)),
            errors=errors,
        )
        logger.info(
            "Order finished (status=%s, filled=%.8g, remaining=%.8g, duration=%.2fs).",
            result.status,
            result.filled,
            result.remaining,
            self._monotonic() - started_at,
        )
        return result

    def cancel_order(self, order_id: str, symbol: str, *, trigger: bool = False) -> OrderSnapshot:
        params = _trigger_params(symbol, trigger)
        if params:
            return _normalize_order(self.exchange.cancel_order(order_id, symbol, params), symbol)
        return _normalize_order(self.exchange.cancel_order(order_id, symbol), symbol)

    def get_order(self, order_id: str, symbol: str, *, trigger: bool = False) -> OrderSnapshot:
        return _normalize_order(
            self._fetch_order(order_id, symbol, _trigger_params(symbol, trigger)), symbol
        )

    def _fetch_order(
        self, order_id: str | None, symbol: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # On Unified accounts ccxt only looks an order up by ID when told the order is among
        # the account's latest 500, which a just-placed or still-protecting order always is.
        return self.exchange.fetch_order(order_id, symbol, {"acknowledged": True, **(params or {})})

    def place_protective_stop(
        self,
        symbol: str,
        amount: float,
        trigger_price: float,
        *,
        dry_run: bool,
        idempotency_key: str,
        side: str = "sell",
    ) -> OrderExecutionResult:
        if amount <= 0 or trigger_price <= 0:
            raise ValueError("Protective stop amount and trigger price must be positive")
        if side not in {"buy", "sell"}:
            raise ValueError("Protective stop side must be 'buy' or 'sell'")
        if dry_run:
            logger.info(
                "Protective stop simulated (symbol=%s, side=%s, trigger=%.8g).",
                symbol,
                side,
                trigger_price,
            )
            return OrderExecutionResult(
                status="dry_run",
                orders=[
                    OrderSnapshot(
                        symbol=symbol,
                        side=side,
                        type="stop_market",
                        status="dry_run",
                        amount=amount,
                        price=trigger_price,
                        raw={"trigger_price": trigger_price},
                    )
                ],
            )
        if not self.settings.bybit_testnet:
            raise ValueError("Live mainnet execution is disabled; Bybit testnet is required")
        if not self.settings.bybit_api_key or not self.settings.bybit_api_secret:
            raise ValueError("Bybit testnet API credentials are required for live execution")
        logger.info(
            "Placing protective stop (symbol=%s, side=%s, trigger=%.8g).",
            symbol,
            side,
            trigger_price,
        )
        link_id = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        # Spot conditional orders infer their direction; ccxt rejects triggerDirection on spot.
        params: dict[str, Any] = {
            "orderLinkId": link_id,
            "triggerPrice": trigger_price,
            "triggerBy": "LastPrice",
        }
        if is_perpetual(symbol):
            # A long's stop sells as the price falls; a short's stop buys as it rises.
            params["triggerDirection"] = "descending" if side == "sell" else "ascending"
            params["reduceOnly"] = True
        try:
            order = self.exchange.create_order(symbol, "market", side, amount, None, params)
        except ccxt.DuplicateOrderId:
            order = self._fetch_order(None, symbol, {"orderLinkId": link_id})
        result = OrderExecutionResult(
            status=order.get("status", "open"),
            orders=[_normalize_order(order, symbol)],
        )
        logger.info("Protective stop response received (status=%s).", result.status)
        return result

    def get_open_orders(self, symbol: str | None = None) -> list[OrderSnapshot]:
        orders = self.exchange.fetch_open_orders(symbol)
        return [_normalize_order(order, symbol or order.get("symbol", "")) for order in orders]

    def position_size(self, symbol: str) -> float:
        """Contracts held in a perpetual position on Bybit right now (0 when flat)."""
        if not is_perpetual(symbol):
            raise ValueError("Only perpetual markets have exchange positions")
        positions = self.exchange.fetch_positions([symbol])
        return sum(
            abs(float(position.get("contracts") or 0))
            for position in positions
            if position.get("symbol") == symbol
        )

    def spot_margin_enabled(self) -> bool:
        """True when spot margin is switched on for the account (it is never switched here)."""
        response = self.exchange.privateGetV5SpotMarginTradeState()
        return str((response.get("result") or {}).get("spotMarginMode")) == "1"

    def repay_borrowed(self, coin: str) -> bool:
        """Repay the coin's floating-rate loan from its free balance, without conversions.

        Bybit refuses repayments from minute 04:00 to 05:30 of every hour; callers retry.
        """
        if not self.settings.bybit_testnet:
            raise ValueError("Live mainnet execution is disabled; Bybit testnet is required")
        response = self.exchange.request(
            "v5/account/no-convert-repay",
            "private",
            "POST",
            {"coin": coin, "repaymentType": "FLEXIBLE"},
        )
        result = response.get("result")
        status = result.get("resultStatus") if isinstance(result, dict) else None
        succeeded = str(response.get("retCode")) == "0" and status in {"SU", "P", None}
        logger.info("Loan repay requested (coin=%s, status=%s).", coin, status)
        return succeeded

    def get_balance(self) -> AccountBalance:
        if not self.settings.bybit_api_key or not self.settings.bybit_api_secret:
            raise ValueError("Bybit API credentials are required to fetch account balance")
        logger.info("Fetching account balance.")
        balance = self.exchange.fetch_balance()
        result = AccountBalance(
            free=balance.get("free") or {},
            used=balance.get("used") or {},
            total=balance.get("total") or {},
            currencies={
                key: value
                for key, value in balance.items()
                if key not in {"free", "used", "total", "info"} and isinstance(value, dict)
            },
            info=balance.get("info") if isinstance(balance.get("info"), dict) else {},
        )
        logger.info("Account balance received (currencies=%d).", len(result.total))
        return result


def _trigger_params(symbol: str, trigger: bool) -> dict[str, Any]:
    # Spot conditional orders live in Bybit's StopOrder list; contract queries see them all.
    return {"trigger": True} if trigger and not is_perpetual(symbol) else {}


def _normalize_order(order: dict[str, Any], symbol: str) -> OrderSnapshot:
    return OrderSnapshot(
        id=str(order["id"]) if order.get("id") is not None else None,
        symbol=order.get("symbol") or symbol,
        type=order.get("type"),
        side=order.get("side"),
        status=order.get("status", "unknown"),
        amount=order.get("amount"),
        price=order.get("price"),
        average=order.get("average"),
        filled=order.get("filled"),
        remaining=order.get("remaining"),
        cost=order.get("cost"),
        timestamp=order.get("timestamp"),
        info=order.get("info") or {},
        raw=order,
    )
