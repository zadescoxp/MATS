import json

from framework.agents.common import parse_tool_response, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import StrategyDefinition
from framework.strategies.library import STRATEGY_LIBRARY, strategy_library_summary

_MAX_CANDIDATES_PER_TIMEFRAME = 5


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
        candidates = _shortlist(candidates)
        diagnostics = {
            "minimum_bars_required": self.context.settings.min_strategy_bars,
            "timeframe_coverage": coverage,
            "eligible_candidates": [
                {
                    "timeframe": item["strategy"].chosen_timeframe,
                    "template_name": item["strategy"].template_name,
                    "parameters": item["strategy"].parameters,
                    "fast_period": item["strategy"].fast_period,
                    "slow_period": item["strategy"].slow_period,
                    "filters": item["strategy"].filters,
                    "stop_loss_pct": item["strategy"].stop_loss_pct,
                    "take_profit_pct": item["strategy"].take_profit_pct,
                    "position_fraction": item["strategy"].position_fraction,
                    "closed_trades": item["trades"],
                    "net_pnl_after_costs": item["net_pnl"],
                    "max_drawdown_pct": item["max_drawdown"],
                }
                for item in candidates
            ],
        }
        if not candidates:
            reason = (
                f"No untried library/timeframe candidate has at least "
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
                    "You may only choose a parameterized variant of a template in the supplied "
                    "reference library, and exactly one candidate from the supplied measured "
                    "candidate list. Never generate arbitrary strategy code. "
                    "The list was tested on historical candles with the configured fees and "
                    "slippage and contains candidates that produced enough closed trades. "
                    "Copy its template name, timeframe, parameters, filters, stop loss, take "
                    "profit, exit rule, and position fraction exactly; do not invent or adjust "
                    "these measured parameters. Prefer candidates with positive net-of-cost PnL "
                    "and "
                    "controlled drawdown, while respecting the multi-timeframe findings. "
                    "Do not choose an untested timeframe. Call "
                    "submit_strategy exactly once. On revision, choose a different candidate "
                    "and apply the concrete critique instructions."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": state.symbol,
                        "strategy_library": strategy_library_summary(),
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
                    (
                        "Submit a supported template and parameters using one measured "
                        "candidate exactly."
                    ),
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
                "Strategy builder selected an untested template, timeframe, or parameter set; "
                "choose one of the supplied measured candidates."
            )
        diagnostics["selected_candidate"] = next(
            item
            for item in diagnostics["eligible_candidates"]
            if item["timeframe"] == strategy.chosen_timeframe
            and item["template_name"] == strategy.template_name
            and item["parameters"] == strategy.parameters
            and item["fast_period"] == strategy.fast_period
            and item["slow_period"] == strategy.slow_period
            and item["filters"] == strategy.filters
            and item["stop_loss_pct"] == strategy.stop_loss_pct
            and item["take_profit_pct"] == strategy.take_profit_pct
            and item["position_fraction"] == strategy.position_fraction
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

            for template_name, template in STRATEGY_LIBRARY.items():
                if timeframe not in template["supported_timeframes"]:
                    continue
                parameters = dict(template["default_parameters"])
                higher_timeframe = parameters.get("higher_timeframe")
                if higher_timeframe:
                    available_higher = [
                        item
                        for item in state.timeframes_to_consider
                        if item in state.raw_data
                        and _timeframe_rank(item) > _timeframe_rank(timeframe)
                    ]
                    if not available_higher:
                        continue
                    parameters["higher_timeframe"] = min(available_higher, key=_timeframe_rank)
                risk = template["default_risk"]
                fast = int(parameters.get("fast_period", parameters.get("macd_fast", 12)))
                slow = int(parameters.get("slow_period", parameters.get("macd_slow", 26)))
                strategy = StrategyDefinition(
                    name=f"{timeframe} {template['name']}",
                    description=str(template["description"]),
                    chosen_timeframe=timeframe,
                    template_name=template_name,
                    entry_rule=template_name,
                    exit_rule="opposite_signal",
                    fast_period=fast,
                    slow_period=slow,
                    parameters=parameters,
                    stop_loss_pct=float(risk["stop_loss_pct"]),
                    take_profit_pct=risk["take_profit_pct"],
                    position_fraction=0.1,
                    filters=["volume_positive"] if template_name == "ema_crossover" else [],
                )
                report = self.context.backtester.run(strategy, candles, data=state.raw_data)
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


def _strategy_key(
    strategy: StrategyDefinition,
) -> tuple[str, str, int, int, str, tuple[str, ...], float, float | None, float]:
    return (
        strategy.template_name,
        strategy.chosen_timeframe,
        strategy.fast_period,
        strategy.slow_period,
        json.dumps(strategy.parameters, sort_keys=True),
        tuple(strategy.filters),
        strategy.stop_loss_pct,
        strategy.take_profit_pct,
        strategy.position_fraction,
    )


def _shortlist(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            item["net_pnl"],
            -item["max_drawdown"],
            item["trades"],
        ),
        reverse=True,
    )
    selected: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    for item in ordered:
        strategy = item["strategy"]
        timeframe = strategy.chosen_timeframe
        if counts.get(timeframe, 0) >= _MAX_CANDIDATES_PER_TIMEFRAME:
            continue
        selected.append(item)
        counts[timeframe] = counts.get(timeframe, 0) + 1
    return selected


def _timeframe_rank(timeframe: str) -> int:
    units = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
    try:
        return int(timeframe[:-1]) * units[timeframe[-1]]
    except (KeyError, ValueError):
        return 0
