from datetime import datetime
from typing import Any

from framework.core.context import AgentContext
from framework.schemas.models import OrderSnapshot, StrategyDefinition
from framework.strategies.market_data import fetch_strategy_ohlcv
from framework.strategies.signals import evaluate_live_signal


class PositionMonitor:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self) -> list[dict[str, Any]]:
        return self.monitor_positions()

    def monitor_positions(self, run_ids: list[str] | None = None) -> list[dict[str, Any]]:
        positions = self.context.database.open_positions()
        if run_ids is not None:
            selected = {position["run_id"]: position for position in positions}
            missing = set(run_ids).difference(selected)
            if missing:
                raise ValueError(f"No open positions found for: {', '.join(sorted(missing))}")
            positions = [selected[run_id] for run_id in run_ids]
        return [self._monitor(position) for position in positions]

    def flatten(self, run_ids: list[str]) -> list[dict[str, Any]]:
        if not run_ids:
            raise ValueError("At least one position must be selected.")
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("Position run IDs must be unique.")
        if self.context.settings.dry_run:
            raise ValueError("Disable dry-run before flattening a live position.")
        positions = {
            position["run_id"]: position for position in self.context.database.open_positions()
        }
        missing = [run_id for run_id in run_ids if run_id not in positions]
        if missing:
            raise ValueError(f"No open positions found for: {', '.join(missing)}")
        return [self._monitor(positions[run_id], force=True) for run_id in run_ids]

    def _monitor(self, position: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        strategy = position["strategy"]
        data = fetch_strategy_ohlcv(
            self.context.market_data, position["symbol"], strategy_model(strategy)
        )
        bars = data[strategy["chosen_timeframe"]]
        latest_bar_timestamp = bars[-1].timestamp.isoformat()
        bar_advanced = latest_bar_timestamp != position["last_bar_timestamp"]
        bars_held = position["bars_held"] + int(bar_advanced)
        opened_at = datetime.fromisoformat(position["opened_at"])
        held_bars = [bar for bar in bars if bar.timestamp >= opened_at]
        position_context = {
            "side": position["side"],
            "entry_price": position["entry_price"],
            "opened_at": position["opened_at"],
            "stop_price": position["stop_price"],
            "take_profit_price": position["take_profit_price"],
            "bars_held": bars_held,
            "peak_price": max([position["entry_price"], *(bar.high for bar in held_bars)]),
            "trough_price": min([position["entry_price"], *(bar.low for bar in held_bars)]),
        }
        signal = evaluate_live_signal(strategy, data, position_context)
        expectancy = self.context.database.recent_strategy_expectancy(
            position["strategy_key"], self.context.settings.edge_fade_lookback
        )
        edge_fade = (
            expectancy is not None and expectancy < self.context.settings.edge_fade_min_expectancy
        )
        if force:
            reason = "Manual flatten requested."
            event_type = "manual_flatten"
        elif edge_fade:
            reason = (
                f"edge faded: rolling expectancy {expectancy:.8g} is below "
                f"{self.context.settings.edge_fade_min_expectancy:.8g}."
            )
            event_type = "edge_fade_exit"
        elif signal.action == "exit":
            reason = f"exit condition met: {signal.reason}"
            event_type = "strategy_stopped"
        else:
            self.context.database.update_position_progress(
                position["run_id"],
                bars_held=bars_held,
                last_bar_timestamp=latest_bar_timestamp,
            )
            return {
                "run_id": position["run_id"],
                "symbol": position["symbol"],
                "action": "hold",
                "reason": signal.reason,
            }

        if self.context.settings.dry_run:
            self.context.database.log_event(
                position["run_id"],
                "monitor_exit_suppressed",
                f"Exit required ({reason}), but dry_run=True.",
            )
            return {
                "run_id": position["run_id"],
                "symbol": position["symbol"],
                "action": "hold",
                "reason": f"Exit required but not sent: {reason}",
            }

        protective_order_id = position.get("protective_order_id")
        if protective_order_id:
            protective_order = self.context.execution.get_order(
                protective_order_id, position["symbol"]
            )
            if protective_order.status in {"closed", "filled"}:
                exit_price = _fill_price([protective_order], signal.price)
                pnl_per_unit = (
                    exit_price - float(position["entry_price"])
                    if position["side"] == "long"
                    else float(position["entry_price"]) - exit_price
                )
                net_pnl = pnl_per_unit * float(protective_order.filled or position["amount"])
                self.context.database.save_execution(
                    f"{position['run_id']}-protective-exit",
                    position["run_id"],
                    [protective_order.model_dump(mode="json")],
                    protective_order.status,
                )
                self.context.database.update_live_position(
                    position["run_id"],
                    status="stopped",
                    event_type=event_type,
                    reason=reason,
                    bars_held=bars_held,
                    net_pnl=net_pnl,
                    last_bar_timestamp=latest_bar_timestamp,
                )
                self.context.database.mark_strategy_stopped(
                    position["symbol"],
                    position["strategy_key"],
                    position["run_id"],
                    reason,
                )
                self.context.database.update_run_status(position["run_id"], "stopped")
                return {
                    "run_id": position["run_id"],
                    "symbol": position["symbol"],
                    "action": event_type,
                    "reason": f"{reason} Protective stop already flattened the position.",
                    "exchange_order_ids": [protective_order.id],
                }
            try:
                if protective_order.status not in {"canceled", "rejected"}:
                    canceled = self.context.execution.cancel_order(
                        protective_order_id, position["symbol"]
                    )
                else:
                    canceled = protective_order
            except Exception as exc:
                self.context.database.log_event(
                    position["run_id"],
                    "monitor_exit_failed",
                    f"Could not cancel protective stop before flattening: {exc}",
                    error=exc,
                )
                raise
            if canceled.status not in {"canceled", "closed", "rejected"}:
                raise RuntimeError(
                    "Protective stop cancellation did not confirm before position flatten."
                )

        exit_side = "sell" if position["side"] == "long" else "buy"
        result = self.context.execution.place_order(
            position["symbol"],
            exit_side,
            float(position["amount"]),
            "market",
            dry_run=False,
            idempotency_key=(f"{position['run_id']}:position-exit:{position['bars_held']}"),
        )
        self.context.database.save_execution(
            f"{position['run_id']}-exit-{bars_held}",
            position["run_id"],
            [order.model_dump(mode="json") for order in result.orders],
            result.status,
        )
        if result.status not in {"filled", "partially_filled"} or result.filled <= 0:
            error = "; ".join(result.errors) or f"Exit order status was {result.status}."
            self.context.database.log_event(position["run_id"], "monitor_exit_failed", error)
            return {
                "run_id": position["run_id"],
                "symbol": position["symbol"],
                "action": "exit_failed",
                "reason": error,
            }

        quantity_closed = result.filled
        exit_price = _fill_price(result.orders, signal.price)
        pnl_per_unit = (
            exit_price - float(position["entry_price"])
            if position["side"] == "long"
            else float(position["entry_price"]) - exit_price
        )
        net_pnl = pnl_per_unit * quantity_closed
        residual = max(float(position["amount"]) - quantity_closed, 0.0)
        status = "stopped" if residual == 0 else "open"
        reason = reason if residual == 0 else f"Partial exit; {residual} units remain. {reason}"
        self.context.database.update_live_position(
            position["run_id"],
            status=status,
            event_type=event_type,
            reason=reason,
            bars_held=bars_held,
            net_pnl=net_pnl if residual == 0 else None,
            amount=residual,
            protective_order_id="" if residual > 0 else None,
            last_bar_timestamp=latest_bar_timestamp,
        )
        if residual == 0:
            self.context.database.mark_strategy_stopped(
                position["symbol"],
                position["strategy_key"],
                position["run_id"],
                reason,
            )
            self.context.database.update_run_status(position["run_id"], "stopped")
        self.context.database.log_event(position["run_id"], event_type, reason)
        return {
            "run_id": position["run_id"],
            "symbol": position["symbol"],
            "action": event_type,
            "reason": reason,
            "exchange_order_ids": [order.id for order in result.orders],
        }


def strategy_model(definition: dict[str, Any]) -> StrategyDefinition:
    return StrategyDefinition.model_validate(definition)


def _fill_price(orders: list[OrderSnapshot], fallback: float) -> float:
    for order in reversed(orders):
        if order.average and order.average > 0:
            return order.average
        if order.price and order.price > 0:
            return order.price
    return fallback
