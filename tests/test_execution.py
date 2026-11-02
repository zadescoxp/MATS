from pydantic import SecretStr

from framework.config.settings import Settings
from framework.tools.execution import ExecutionTools


class PartialFillExchange:
    def __init__(self) -> None:
        self.fetches = 0
        self.canceled: list[str] = []
        self.created_params: list[dict] = []

    def create_order(self, symbol, order_type, side, amount, price, params):
        self.created_params.append(params)
        return {
            "id": "order-1",
            "status": "open",
            "filled": 0,
            "amount": amount,
            "remaining": amount,
        }

    def fetch_order(self, order_id, symbol, params=None):
        self.fetches += 1
        if self.fetches == 1:
            return {
                "id": order_id,
                "status": "open",
                "filled": 0.3,
                "amount": 1,
                "remaining": 0.7,
            }
        return {
            "id": order_id,
            "status": "canceled",
            "filled": 0.3,
            "amount": 1,
            "remaining": 0.7,
            "trades": [{"id": "fill-1", "amount": 0.3}],
        }

    def cancel_order(self, order_id, symbol):
        self.canceled.append(order_id)
        return {"id": order_id, "status": "canceled"}


def test_execution_monitors_and_reports_partial_fill() -> None:
    settings = Settings(
        bybit_api_key=SecretStr("test-key"),
        bybit_api_secret=SecretStr("test-secret"),
        bybit_testnet=True,
        execution_timeout_seconds=1,
        execution_poll_interval_seconds=1,
    )
    clock = [0.0]
    exchange = PartialFillExchange()
    tools = ExecutionTools(
        settings,
        exchange=exchange,
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        monotonic=lambda: clock[0],
    )
    result = tools.place_order(
        "BTC/USDT",
        "buy",
        1,
        dry_run=False,
        idempotency_key="run-entry",
    )
    assert result.status == "partially_filled"
    assert result.filled == 0.3
    assert exchange.canceled == ["order-1"]
    assert result.errors


def test_live_order_is_refused_when_mainnet_selected() -> None:
    settings = Settings(
        bybit_api_key=SecretStr("test-key"),
        bybit_api_secret=SecretStr("test-secret"),
        bybit_testnet=False,
    )
    tools = ExecutionTools(settings, exchange=PartialFillExchange())
    try:
        tools.place_order(
            "BTC/USDT",
            "buy",
            1,
            dry_run=False,
            idempotency_key="run-entry",
        )
    except ValueError as exc:
        assert "testnet is required" in str(exc)
    else:
        raise AssertionError("Mainnet order unexpectedly accepted")


def test_protective_stop_is_created_as_testnet_conditional_order() -> None:
    settings = Settings(
        bybit_api_key=SecretStr("test-key"),
        bybit_api_secret=SecretStr("test-secret"),
        bybit_testnet=True,
    )
    exchange = PartialFillExchange()
    tools = ExecutionTools(settings, exchange=exchange)
    order = tools.place_protective_stop(
        "BTC/USDT",
        0.1,
        90,
        dry_run=False,
        idempotency_key="run-stop",
    )
    assert order.orders[0].id == "order-1"
    assert exchange.created_params[0]["triggerPrice"] == 90
    assert "triggerDirection" not in exchange.created_params[0]


class ClosedFillExchange(PartialFillExchange):
    def create_order(self, symbol, order_type, side, amount, price, params):
        self.created_params.append(params)
        return {"id": "order-2", "status": "closed", "filled": 0.0999, "amount": 0.1}


def test_market_order_is_sized_in_base_units_and_closed_fill_counts_as_filled() -> None:
    settings = Settings(
        bybit_api_key=SecretStr("test-key"),
        bybit_api_secret=SecretStr("test-secret"),
        bybit_testnet=True,
    )
    exchange = ClosedFillExchange()
    tools = ExecutionTools(settings, exchange=exchange)
    result = tools.place_order(
        "BTC/USDT",
        "buy",
        0.1,
        dry_run=False,
        idempotency_key="run-entry",
    )
    assert exchange.created_params[0]["marketUnit"] == "baseCoin"
    assert result.status == "filled"
    assert result.filled == 0.0999


def test_borrowing_order_is_sent_as_a_spot_margin_order() -> None:
    settings = Settings(
        bybit_api_key=SecretStr("test-key"),
        bybit_api_secret=SecretStr("test-secret"),
        bybit_testnet=True,
    )
    exchange = ClosedFillExchange()
    tools = ExecutionTools(settings, exchange=exchange)

    tools.place_order("BTC/USDT", "buy", 0.1, dry_run=False, idempotency_key="a", borrow=True)
    tools.place_order("BTC/USDT", "buy", 0.1, dry_run=False, idempotency_key="b")

    assert exchange.created_params[0]["isLeverage"] == 1
    assert "isLeverage" not in exchange.created_params[1]


class BybitReplyExchange:
    """Bybit answers order creation with IDs only; ccxt leaves status None."""

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.fetch_params: list[dict] = []

    def create_order(self, symbol, order_type, side, amount, price, params):
        order_id = f"order-{len(self.orders) + 1}"
        trigger = "triggerPrice" in params
        self.orders[order_id] = {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "status": "open" if trigger else "closed",
            "amount": amount,
            "filled": 0 if trigger else amount,
            "remaining": 0,
            "average": 100,
        }
        return {"id": order_id, "symbol": symbol, "status": None, "filled": None, "amount": None}

    def fetch_order(self, order_id, symbol, params=None):
        self.fetch_params.append(params or {})
        return self.orders[order_id]

    def cancel_order(self, order_id, symbol, params=None):
        self.orders[order_id]["status"] = "canceled"
        return {"id": order_id, "symbol": symbol, "status": None}


def test_order_replies_without_a_status_are_looked_up() -> None:
    settings = Settings(
        bybit_api_key=SecretStr("test-key"),
        bybit_api_secret=SecretStr("test-secret"),
        bybit_testnet=True,
        execution_poll_interval_seconds=0.01,
    )
    exchange = BybitReplyExchange()
    tools = ExecutionTools(settings, exchange=exchange, sleep=lambda _seconds: None)

    entry = tools.place_order("BTC/USDT:USDT", "sell", 0.001, dry_run=False, idempotency_key="e")
    stop = tools.place_protective_stop(
        "BTC/USDT:USDT", 0.001, 105, dry_run=False, idempotency_key="s", side="buy"
    )

    assert entry.status == "filled" and entry.filled == 0.001
    assert entry.orders[0].status == "unknown"  # the bare create reply
    assert stop.status == "open" and stop.orders[0].id == "order-2"
    assert tools.cancel_order("order-2", "BTC/USDT:USDT", trigger=True).status == "canceled"
    assert all(params.get("acknowledged") for params in exchange.fetch_params)
