from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.tools.cost_model import CostAssumptions


class BacktestNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> dict[str, object]:
        if state.strategy is None:
            raise ValueError("Backtest requires a strategy")
        candles = state.raw_data.get(state.strategy.chosen_timeframe)
        if not candles:
            raise ValueError("No candles are available for the strategy timeframe")
        capital = (
            state.account_equity
            if state.account_equity is not None
            else self.context.settings.initial_capital
        )
        report = self.context.backtester.run(state.strategy, candles, initial_capital=capital)
        strategy_id = f"{state.run_id}-v{state.revision_count}"
        backtest_id = strategy_id
        costs = CostAssumptions(
            maker_fee_rate=self.context.settings.fee_rate_maker,
            taker_fee_rate=self.context.settings.fee_rate_taker,
            slippage_percent=self.context.settings.slippage_percent,
            slippage_fixed=self.context.settings.slippage_fixed,
        )
        self.context.database.save_backtest(
            backtest_id,
            strategy_id,
            report.model_dump(mode="json", exclude={"equity_curve"}),
            report.model_dump(mode="json")["equity_curve"],
            {**costs.model_dump(mode="json"), "funding_cost": 0.0},
        )
        return {"backtest_report": report}
