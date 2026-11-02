import logging

from framework.agents.strategy_builder import strategy_series
from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.tools.cost_model import CostAssumptions

logger = logging.getLogger(__name__)


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
        strategy = state.strategy
        # The same series the builder measured with: without the higher timeframe or the
        # reference pair, multi-timeframe and pair templates would never signal.
        series = strategy_series(
            state, strategy.chosen_timeframe, strategy.template_name, dict(strategy.parameters)
        )
        report = self.context.backtester.run(
            strategy, candles, data=series or dict(state.raw_data), initial_capital=capital
        )
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
        logger.info(
            "Backtest complete (trades=%d, net_pnl=%.4f, return=%.2f%%, max_drawdown=%.2f%%).",
            report.total_trades,
            report.net_pnl,
            report.net_return_pct * 100,
            report.max_drawdown_pct * 100,
        )
        return {"backtest_report": report}
