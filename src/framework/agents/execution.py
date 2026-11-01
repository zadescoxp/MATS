from collections.abc import Sequence
from typing import Any

from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.schemas.models import (
    ExecutionReport,
    LiveSignal,
    OHLCVBar,
    OrderSnapshot,
)
from framework.strategies.library import strategy_fingerprint
from framework.strategies.market_data import fetch_strategy_ohlcv
from framework.strategies.signals import evaluate_live_signal


class ExecutionNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> dict[str, ExecutionReport | LiveSignal | str]:
        settings = self.context.settings
        self.context.database.log_event(
            state.run_id,
            "execution_started",
            f"Execution start: dry_run={settings.dry_run}, kill_switch={settings.kill_switch}.",
        )
        if not state.critique or state.critique.decision != "APPROVE":
            raise ValueError("Execution requires Critique approval")
        if not state.risk_assessment:
            raise ValueError("Execution requires a sized risk plan")
        if state.strategy is None:
            raise ValueError("Execution requires an approved strategy")

        data = fetch_strategy_ohlcv(self.context.market_data, state.symbol, state.strategy)
        signal = evaluate_live_signal(state.strategy, data)
        dry_run = settings.dry_run or settings.kill_switch
        fingerprint = strategy_fingerprint(state.strategy)
        existing_position = self.context.database.open_positions(state.symbol)

        reason = signal.reason
        if existing_position:
            reason = (
                f"Order suppressed because run {existing_position[0]['run_id']} "
                "already has an open position for this symbol."
            )
        elif self.context.database.strategy_stop_reason(state.symbol, fingerprint):
            reason = "This exact strategy was previously stopped and cannot re-enter."
        elif signal.action not in {"long", "short"}:
            reason = signal.reason
        elif signal.action == "short" and not settings.allow_short:
            reason = "Short entry is disabled; the spot strategy will remain flat."
        elif signal.action == "short" and not dry_run:
            reason = (
                "The Bybit spot path cannot open a borrowed or derivative short; "
                "the short signal is not sent as a sell order."
            )
        elif not state.risk_assessment.approved or state.risk_assessment.position_size <= 0:
            reason = "Risk assessment sized this live signal at zero."
        else:
            return self._place_entry(state, signal, data, dry_run, fingerprint)

        report = ExecutionReport(
            status="hold",
            orders=[],
            fills=[],
            errors=[reason],
            dry_run=dry_run,
            live_signal=signal,
            execution_result=f"No order placed: {reason}",
        )
        self._save_report(state, report)
        return {
            "execution_result": report,
            "live_signal": signal,
            "status": "completed",
        }

    def _place_entry(
        self,
        state: GraphState,
        signal: LiveSignal,
        data: dict[str, Sequence[OHLCVBar]],
        dry_run: bool,
        fingerprint: str,
    ) -> dict[str, ExecutionReport | LiveSignal | str]:
        risk = state.risk_assessment
        if risk is None:
            raise ValueError("Execution requires a sized risk plan")
        if signal.action != risk.side:
            reason = (
                f"Live signal changed from sized side {risk.side} to {signal.action}; "
                "no order placed."
            )
            report = ExecutionReport(
                status="hold",
                errors=[reason],
                dry_run=dry_run,
                live_signal=signal,
                execution_result=f"No order placed: {reason}",
            )
            self._save_report(state, report)
            return {
                "execution_result": report,
                "live_signal": signal,
                "status": "completed",
            }

        stop = signal.stop or risk.stop_price
        stop_distance = abs(signal.price - stop)
        if stop_distance <= 0:
            raise ValueError("Live signal stop must differ from its current price")
        amount = min(risk.position_size, risk.risk_amount / stop_distance)
        if amount <= 0:
            reason = "Live stop distance produced a zero risk-sized order."
            report = ExecutionReport(
                status="hold",
                errors=[reason],
                dry_run=dry_run,
                live_signal=signal,
                execution_result=f"No order placed: {reason}",
            )
            self._save_report(state, report)
            return {
                "execution_result": report,
                "live_signal": signal,
                "status": "completed",
            }

        side = "buy" if signal.action == "long" else "sell"
        self.context.database.log_event(
            state.run_id,
            "algorithm_order",
            (
                f"Bar {data[state.strategy.chosen_timeframe][-1].timestamp.isoformat()} "
                f"action={signal.action} amount={amount:.8g}. Placing one order."
            ),
        )
        outcome = self.context.execution.place_order(
            state.symbol,
            side,
            amount,
            "market",
            dry_run=dry_run,
            idempotency_key=f"{state.run_id}:spot-entry",
        )
        orders = [_order_dict(order) for order in outcome.orders]
        errors = list(outcome.errors)
        entry_price = (
            outcome.orders[0].average
            if outcome.orders and outcome.orders[0].average
            else signal.price
        )
        protective_order_id: str | None = None
        protective_orders: list[OrderSnapshot] = []
        emergency_orders: list[OrderSnapshot] = []
        emergency_pnl: float | None = None
        emergency_flattened = False
        filled = outcome.filled
        if outcome.status == "filled" and filled <= 0:
            filled = amount
        if outcome.status == "dry_run":
            stop_result = self.context.execution.place_protective_stop(
                state.symbol,
                amount,
                stop,
                dry_run=True,
                idempotency_key=f"{state.run_id}:spot-stop",
                side="sell" if signal.action == "long" else "buy",
            )
            protective_orders = stop_result.orders
            orders.extend(_order_dict(order) for order in protective_orders)
        elif outcome.status in {"filled", "partially_filled"} and filled > 0:
            try:
                stop_result = self.context.execution.place_protective_stop(
                    state.symbol,
                    filled,
                    stop,
                    dry_run=False,
                    idempotency_key=f"{state.run_id}:spot-stop",
                    side="sell" if signal.action == "long" else "buy",
                )
                protective_orders = stop_result.orders
                orders.extend(_order_dict(order) for order in protective_orders)
                errors.extend(stop_result.errors)
                protective_order_id = (
                    stop_result.orders[0].id
                    if stop_result.status in {"open", "filled", "partially_filled"}
                    and stop_result.orders
                    and stop_result.orders[0].id
                    and not stop_result.errors
                    else None
                )
            except Exception as exc:
                errors.append(f"Protective stop placement failed: {exc}")
                self.context.database.log_event(
                    state.run_id,
                    "protective_stop_failed",
                    str(exc),
                    error=exc,
                )
            if protective_order_id is None:
                errors.append(
                    "No confirmed protective stop was recorded; attempting an immediate "
                    "market flatten."
                )
                try:
                    flatten = self.context.execution.place_order(
                        state.symbol,
                        "sell" if signal.action == "long" else "buy",
                        filled,
                        "market",
                        dry_run=False,
                        idempotency_key=f"{state.run_id}:protective-failure-exit",
                    )
                    emergency_orders = flatten.orders
                    orders.extend(_order_dict(order) for order in emergency_orders)
                    errors.extend(flatten.errors)
                    emergency_flattened = flatten.status == "filled" and flatten.filled >= filled
                    closed_quantity = flatten.filled
                    if flatten.status == "filled" and closed_quantity <= 0:
                        closed_quantity = filled
                    if emergency_flattened:
                        exit_price = _fill_price(emergency_orders, signal.price)
                        pnl_per_unit = (
                            exit_price - entry_price
                            if signal.action == "long"
                            else entry_price - exit_price
                        )
                        emergency_pnl = pnl_per_unit * filled
                    filled = max(filled - closed_quantity, 0)
                except Exception as exc:
                    errors.append(f"Immediate market flatten failed: {exc}")
                    self.context.database.log_event(
                        state.run_id,
                        "emergency_flatten_failed",
                        str(exc),
                        error=exc,
                    )
            entry_order_id = outcome.orders[0].id if outcome.orders else None
            if entry_order_id is None:
                errors.append("Filled entry response did not include an exchange order ID.")
            self.context.database.save_live_position(
                run_id=state.run_id,
                symbol=state.symbol,
                strategy_key=fingerprint,
                strategy=state.strategy.model_dump(mode="json"),
                side=signal.action,
                amount=outcome.filled or amount,
                entry_price=entry_price,
                stop_price=stop,
                take_profit_price=(
                    entry_price * (1 + state.strategy.take_profit_pct)
                    if signal.action == "long" and state.strategy.take_profit_pct
                    else entry_price * (1 - state.strategy.take_profit_pct)
                    if state.strategy.take_profit_pct
                    else None
                ),
                entry_order_id=entry_order_id,
                protective_order_id=protective_order_id,
                entry_bar_timestamp=data[state.strategy.chosen_timeframe][-1].timestamp.isoformat(),
            )
            if emergency_flattened:
                self.context.database.update_live_position(
                    state.run_id,
                    status="stopped",
                    event_type="strategy_stopped",
                    reason="Protective stop placement failed; position was immediately flattened.",
                    bars_held=0,
                    net_pnl=emergency_pnl,
                    amount=0,
                )
                self.context.database.mark_strategy_stopped(
                    state.symbol,
                    fingerprint,
                    state.run_id,
                    "Protective stop placement failed; position was immediately flattened.",
                )
            elif filled < (outcome.filled or amount):
                self.context.database.update_live_position(
                    state.run_id,
                    status="open",
                    event_type="emergency_partial_exit",
                    reason="Protective stop placement failed; residual position remains open.",
                    bars_held=0,
                    amount=filled,
                )

        report = ExecutionReport(
            status="stopped" if emergency_flattened else outcome.status,
            orders=orders,
            fills=outcome.fills,
            errors=errors,
            dry_run=outcome.status == "dry_run" or dry_run,
            live_signal=signal,
            execution_result=(
                f"Live signal {signal.action}: {signal.reason} "
                f"Entry order IDs: {_order_ids(outcome.orders)}; "
                f"protective stop IDs: {_order_ids(protective_orders)}; "
                f"emergency exit IDs: {_order_ids(emergency_orders)}."
            ),
        )
        self._save_report(state, report)
        return {
            "execution_result": report,
            "live_signal": signal,
            "status": "completed",
        }

    def _save_report(self, state: GraphState, report: ExecutionReport) -> None:
        self.context.database.save_execution(
            f"{state.run_id}-execution",
            state.run_id,
            report.model_dump(mode="json")["orders"],
            report.status,
        )
        self.context.database.log_event(
            state.run_id,
            "execution_result",
            report.execution_result or report.status,
        )


def _order_dict(order: OrderSnapshot) -> dict[str, Any]:
    result = order.model_dump(mode="json")
    if result.get("id") is not None:
        result["exchange_order_id"] = result["id"]
    return result


def _order_ids(orders: Sequence[OrderSnapshot]) -> list[str | None]:
    return [order.id for order in orders]


def _fill_price(orders: Sequence[OrderSnapshot], fallback: float) -> float:
    for order in reversed(orders):
        if order.average and order.average > 0:
            return order.average
        if order.price and order.price > 0:
            return order.price
    return fallback
