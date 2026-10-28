import json

from framework.agents.common import parse_tool_response, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import StrategyDefinition

_EMA_PAIRS = ((5, 10), (8, 21), (9, 21), (12, 26), (14, 32), (20, 50))
_FILTER_SETS = (
    (),
    ("rsi_below_70",),
    ("price_above_slow_ema",),
    ("rsi_below_70", "price_above_slow_ema"),
)


class StrategyBuilderNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> AgentResult:
        if not state.analyst_findings:
            raise ValueError("Strategy builder requires analyst findings")

        candidates, coverage = self._candidate_strategies(state)
        previous_key = _strategy_key(state.strategy) if state.strategy else None
        candidates = [
            item for item in candidates if _strategy_key(item["strategy"]) != previous_key
        ]
        diagnostics = {
            "minimum_bars_required": self.context.settings.min_strategy_bars,
            "timeframe_coverage": coverage,
            "eligible_candidates": [
                {
                    "timeframe": item["strategy"].chosen_timeframe,
                    "fast_period": item["strategy"].fast_period,
                    "slow_period": item["strategy"].slow_period,
                    "filters": item["strategy"].filters,
                    "closed_trades": item["trades"],
                    "net_pnl_after_costs": item["net_pnl"],
                    "max_drawdown_pct": item["max_drawdown"],
                }
                for item in candidates
            ],
        }
        if not candidates:
            reason = (
                f"No untried EMA/timeframe candidate has at least "
                f"{self.context.settings.min_strategy_bars} historical bars and "
                f"at least {self.context.settings.min_backtest_trades} closed trades "
                "after fees and slippage."
            )
            if previous_key:
                reason += " All currently viable candidates were already attempted."
            else:
                reason += " Review timeframe_coverage for missing or insufficient history."
            return AgentResult(
                updates={
                    "strategy": None,
                    "strategy_diagnostics": diagnostics,
                    "status": "failed",
                    "errors": [*state.errors, reason],
                }
            )

        instructions = (
            state.critique.revision_instructions
            if state.critique and state.critique.decision == "REJECT"
            else "Choose the best evidence-supported candidate."
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Choose exactly one candidate from the supplied measured candidate list. "
                    "The list was tested on historical candles with the configured fees and "
                    "slippage and contains candidates that produced enough closed trades. "
                    "Choose its timeframe, fast/slow periods, and filters exactly; do not "
                    "invent parameters or filters. You may set a sensible stop loss, take "
                    "profit, and position fraction. Prefer credible net-of-cost evidence and "
                    "appropriate multi-timeframe findings, not an untested timeframe. Call "
                    "submit_strategy exactly once. On revision, choose a different candidate "
                    "and apply the concrete critique instructions."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": state.symbol,
                        "analyst_findings": [
                            item.model_dump(mode="json") for item in state.analyst_findings
                        ],
                        "previous_strategy": (
                            state.strategy.model_dump(mode="json") if state.strategy else None
                        ),
                        "critique_revision_instructions": instructions,
                        "revision_count": state.revision_count,
                        "timeframe_coverage": coverage,
                        "eligible_candidates": diagnostics["eligible_candidates"],
                    },
                    separators=(",", ":"),
                ),
            },
        ]
        response = self.context.llm.invoke_with_tools(
            messages,
            [
                submit_tool(
                    "submit_strategy",
                    StrategyDefinition,
                    "Submit a complete strategy using one measured candidate exactly.",
                )
            ],
            self.context.settings.model_for("strategy"),
        )
        strategy = parse_tool_response(response, "submit_strategy", StrategyDefinition)
        selected = next(
            (
                item
                for item in candidates
                if _strategy_key(item["strategy"]) == _strategy_key(strategy)
            ),
            None,
        )
        if selected is None:
            raise ValueError(
                "Strategy builder selected an untested timeframe, EMA pair, or filter set; "
                "choose one of the supplied measured candidates."
            )
        diagnostics["selected_candidate"] = next(
            item
            for item in diagnostics["eligible_candidates"]
            if item["timeframe"] == strategy.chosen_timeframe
            and item["fast_period"] == strategy.fast_period
            and item["slow_period"] == strategy.slow_period
            and item["filters"] == strategy.filters
        )
        strategy_id = f"{state.run_id}-v{state.revision_count}"
        self.context.database.save_strategy(
            strategy_id,
            state.run_id,
            strategy.model_dump(mode="json"),
            state.revision_count,
        )
        history = [
            *state.messages,
            {"role": "assistant", "content": response.content, "tool": "submit_strategy"},
        ]
        return result_with_response(
            {"strategy": strategy, "strategy_diagnostics": diagnostics},
            response,
            history,
        )

    def _candidate_strategies(
        self, state: GraphState
    ) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
        candidates: list[dict[str, object]] = []
        coverage: dict[str, dict[str, object]] = {}
        for timeframe, candles in state.raw_data.items():
            bar_count = len(candles)
            info: dict[str, object] = {
                "bars": bar_count,
                "first_timestamp": candles[0].timestamp.isoformat() if candles else None,
                "last_timestamp": candles[-1].timestamp.isoformat() if candles else None,
                "eligible": bar_count >= self.context.settings.min_strategy_bars,
            }
            coverage[timeframe] = info
            if bar_count < self.context.settings.min_strategy_bars:
                info["reason"] = "insufficient_history"
                continue

            for fast, slow in _EMA_PAIRS:
                for filters in _FILTER_SETS:
                    strategy = StrategyDefinition(
                        name=f"{timeframe} EMA {fast}/{slow}",
                        description="Historical candidate evaluated with configured spot costs.",
                        chosen_timeframe=timeframe,
                        entry_rule="ema_crossover",
                        exit_rule="ema_crossover",
                        fast_period=fast,
                        slow_period=slow,
                        filters=list(filters),
                    )
                    report = self.context.backtester.run(strategy, candles)
                    if report.total_trades >= self.context.settings.min_backtest_trades:
                        candidates.append(
                            {
                                "strategy": strategy,
                                "trades": report.total_trades,
                                "net_pnl": report.net_pnl,
                                "max_drawdown": report.max_drawdown_pct,
                            }
                        )
        return candidates, coverage


def _strategy_key(strategy: StrategyDefinition) -> tuple[str, int, int, tuple[str, ...]]:
    return (
        strategy.chosen_timeframe,
        strategy.fast_period,
        strategy.slow_period,
        tuple(strategy.filters),
    )
