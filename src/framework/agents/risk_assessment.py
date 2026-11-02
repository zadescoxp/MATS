import logging

from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.schemas.models import LiveSignal, RiskAssessment
from framework.strategies.market_data import fetch_strategy_ohlcv
from framework.strategies.signals import evaluate_live_signal
from framework.tools.account import coin_detail

logger = logging.getLogger(__name__)


class RiskAssessmentNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> dict[str, RiskAssessment | LiveSignal]:
        if not state.critique or state.critique.decision != "APPROVE":
            raise ValueError("Risk assessment is only allowed after Critique approval")
        if state.strategy is None:
            raise ValueError("Risk assessment requires an approved strategy")

        live_data = fetch_strategy_ohlcv(self.context.market_data, state.symbol, state.strategy)
        signal = evaluate_live_signal(state.strategy, live_data)
        equity = state.account_equity
        equity_source = "run override"
        quote_currency = state.symbol.split("/")[-1]
        balance_notes: list[str] = []
        live = not (self.context.settings.dry_run or self.context.settings.kill_switch)
        leverage = state.leverage or self.context.settings.spot_leverage
        if leverage > 1 and signal.action != "long":
            leverage = 1.0  # only long spot entries borrow
        # Live orders can only use money the Bybit account actually holds right now.
        free_quote: float | None = None
        borrow_capacity = 0.0
        if signal.action in {"long", "short"} and live:
            balance = self.context.execution.get_balance()
            free_quote = balance.available(quote_currency)
            if leverage > 1:
                if self.context.execution.spot_margin_enabled():
                    borrow_capacity = min(
                        free_quote * (leverage - 1),
                        coin_detail(balance, quote_currency, "availableToBorrow"),
                    )
                else:
                    balance_notes.append(
                        "Spot margin is off on the Bybit account, so this run trades without "
                        "leverage. Turn spot margin on in Bybit to borrow."
                    )
                    leverage = 1.0
            if equity is None:
                equity = free_quote
                equity_source = "testnet available balance"
            elif equity > free_quote:
                balance_notes.append(
                    f"Run capital {equity:,.2f} is more than the {free_quote:,.2f} "
                    f"{quote_currency} available on Bybit; sized on the available balance."
                )
                equity = free_quote
                equity_source = "testnet available balance"
            if free_quote <= 0:
                balance_notes.append(
                    f"No {quote_currency} is available on the Bybit account; size is zero."
                )
                equity = self.context.settings.initial_capital  # keeps the plan readable
        if equity is None:
            equity = self.context.settings.initial_capital
            equity_source = "configured initial capital"
        if equity <= 0:
            raise ValueError("Account equity must be positive")
        current_price = signal.price
        if not current_price or current_price <= 0:
            raise ValueError("Risk assessment requires a current positive market price")

        constraints: list[str] = list(balance_notes)
        if signal.action not in {"long", "short"}:
            constraints.append(f"Live signal is {signal.action}: {signal.reason} Size is zero.")
        if signal.action == "short" and not self.context.settings.allow_short:
            constraints.append("Short entry is disabled; size is zero.")
        constraints.append(f"Risk sized using {equity_source}.")
        if leverage > 1:
            borrow_note = (
                f" Up to {borrow_capacity:,.2f} {quote_currency} may be borrowed."
                if free_quote is not None
                else " Dry run: no borrowing happens."
            )
            constraints.append(
                f"Leverage {leverage:g}x raises the exposure limit; the loss at the stop is "
                f"still capped at {self.context.settings.max_risk_per_trade:.1%} of equity."
                + borrow_note
            )

        stop_price = signal.stop or current_price * (1 - state.strategy.stop_loss_pct)
        stop_distance = abs(current_price - stop_price)
        if stop_distance <= 0:
            raise ValueError("Live signal stop must be different from the current price")
        risk_budget = equity * self.context.settings.max_risk_per_trade
        exposure_cap = equity * self.context.settings.max_account_exposure
        strategy_cap = equity * state.strategy.position_fraction
        # Leverage lifts the exposure caps; the loss budget at the stop stays the same.
        caps = [
            risk_budget / stop_distance,
            exposure_cap * leverage / current_price,
            strategy_cap * leverage / current_price,
        ]
        if free_quote is not None:
            costs = self.context.settings.fee_rate_taker + self.context.settings.slippage_percent
            buying_power = max(free_quote, 0.0) + borrow_capacity
            caps.append(buying_power / (current_price * (1 + costs)))
        amount = min(caps)
        if free_quote is not None and free_quote <= 0:
            amount = 0.0
        if (
            signal.action not in {"long", "short"}
            or signal.action == "short"
            and not self.context.settings.allow_short
        ):
            amount = 0.0
        notional = amount * current_price
        risk_amount = amount * stop_distance
        assessment = RiskAssessment(
            approved=amount > 0,
            side=signal.action if signal.action in {"long", "short"} else "long",
            position_size=amount,
            position_notional=notional,
            risk_amount=risk_amount,
            max_concurrent_exposure=notional / equity,
            stop_price=stop_price,
            account_constraints=constraints,
        )
        logger.info(
            "Risk plan assessed (signal=%s, approved=%s, size=%.8g, notional=%.4f, "
            "risk=%.4f, equity_source=%s).",
            signal.action,
            assessment.approved,
            assessment.position_size,
            assessment.position_notional,
            assessment.risk_amount,
            equity_source,
        )
        return {"risk_assessment": assessment, "live_signal": signal}
