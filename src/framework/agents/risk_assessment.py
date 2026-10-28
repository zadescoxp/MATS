from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.schemas.models import RiskAssessment


class RiskAssessmentNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> dict[str, RiskAssessment]:
        if not state.critique or state.critique.decision != "APPROVE":
            raise ValueError("Risk assessment is only allowed after Critique approval")
        if state.strategy is None:
            raise ValueError("Risk assessment requires an approved strategy")

        equity = state.account_equity
        equity_source = "run override"
        if equity is None and not (
            self.context.settings.dry_run or self.context.settings.kill_switch
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
        ticker = state.market_context.get("ticker", {})
        current_price = ticker.get("last")
        if not current_price:
            candles = state.raw_data.get(state.strategy.chosen_timeframe, [])
            current_price = candles[-1].close if candles else None
        if not current_price or current_price <= 0:
            raise ValueError("Risk assessment requires a current positive market price")

        findings = state.analyst_findings[0] if state.analyst_findings else None
        entry_aligned = bool(findings and findings.lower_timeframe_timing == "bullish")
        constraints: list[str] = []
        if not entry_aligned:
            constraints.append("No bullish lower-timeframe entry timing; order size is zero.")
        constraints.append(f"Risk sized using {equity_source}.")

        stop_distance = current_price * state.strategy.stop_loss_pct
        risk_budget = equity * self.context.settings.max_risk_per_trade
        exposure_cap = equity * self.context.settings.max_account_exposure
        strategy_cap = equity * state.strategy.position_fraction
        amount = min(
            risk_budget / stop_distance,
            exposure_cap / current_price,
            strategy_cap / current_price,
        )
        if not entry_aligned:
            amount = 0.0
        notional = amount * current_price
        risk_amount = notional * state.strategy.stop_loss_pct
        assessment = RiskAssessment(
            approved=amount > 0,
            position_size=amount,
            position_notional=notional,
            risk_amount=risk_amount,
            max_concurrent_exposure=notional / equity,
            stop_price=current_price * (1 - state.strategy.stop_loss_pct),
            account_constraints=constraints,
        )
        return {"risk_assessment": assessment}
