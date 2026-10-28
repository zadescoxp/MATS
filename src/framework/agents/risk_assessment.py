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

        equity = state.account_equity or self.context.settings.initial_capital
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
        if not state.account_equity:
            constraints.append("Configured initial capital is used until account equity is supplied.")

        stop_distance = current_price * state.strategy.stop_loss_pct
        risk_budget = equity * self.context.settings.max_risk_per_trade
        exposure_cap = equity * self.context.settings.max_account_exposure
        strategy_cap = equity * state.strategy.position_fraction
        amount = min(risk_budget / stop_distance, exposure_cap / current_price, strategy_cap / current_price)
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
