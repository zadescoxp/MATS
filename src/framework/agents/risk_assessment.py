from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.schemas.models import LiveSignal, RiskAssessment
from framework.strategies.market_data import fetch_strategy_ohlcv
from framework.strategies.signals import evaluate_live_signal


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
        if (
            signal.action in {"long", "short"}
            and equity is None
            and not (self.context.settings.dry_run or self.context.settings.kill_switch)
        ):
            balance = self.context.execution.get_balance()
            quote_currency = state.symbol.split("/")[-1]
            free_balance = balance.free.get(quote_currency)
            if free_balance is None:
                free_balance = balance.currencies.get(quote_currency, {}).get("free")
            if free_balance is None or float(free_balance) <= 0:
                raise ValueError(f"No available {quote_currency} balance for testnet risk sizing")
            equity = float(free_balance)
            equity_source = "testnet available balance"
        if equity is None:
            equity = self.context.settings.initial_capital
            equity_source = "configured initial capital"
        if equity <= 0:
            raise ValueError("Account equity must be positive")
        current_price = signal.price
        if not current_price or current_price <= 0:
            raise ValueError("Risk assessment requires a current positive market price")

        constraints: list[str] = []
        if signal.action not in {"long", "short"}:
            constraints.append(f"Live signal is {signal.action}: {signal.reason} Size is zero.")
        if signal.action == "short" and not self.context.settings.allow_short:
            constraints.append("Short entry is disabled; size is zero.")
        constraints.append(f"Risk sized using {equity_source}.")

        stop_price = signal.stop or current_price * (1 - state.strategy.stop_loss_pct)
        stop_distance = abs(current_price - stop_price)
        if stop_distance <= 0:
            raise ValueError("Live signal stop must be different from the current price")
        risk_budget = equity * self.context.settings.max_risk_per_trade
        exposure_cap = equity * self.context.settings.max_account_exposure
        strategy_cap = equity * state.strategy.position_fraction
        amount = min(
            risk_budget / stop_distance,
            exposure_cap / current_price,
            strategy_cap / current_price,
        )
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
        return {"risk_assessment": assessment, "live_signal": signal}
