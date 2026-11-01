import json
import logging

from framework.agents.common import parse_tool_response, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import StrategyDefinition
from framework.strategies.library import (
    REFERENCE_EXAMPLES,
    STRATEGY_LIBRARY,
    strategy_fingerprint,
    strategy_library_summary,
)
from framework.strategies.regime import diagnose_regime

_MAX_CANDIDATES_PER_TIMEFRAME = 5
logger = logging.getLogger(__name__)


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
        logger.info(
            "Measured strategy candidates (eligible=%d, timeframes=%d).",
            len(candidates),
            len(coverage),
        )
        for index, item in enumerate(candidates, start=1):
            item["candidate_id"] = f"c{index}"
        diagnostics = {
            "minimum_bars_required": self.context.settings.min_strategy_bars,
            "timeframe_coverage": coverage,
            "eligible_candidates": [
                {
                    "candidate_id": item["candidate_id"],
                    "timeframe": item["strategy"].chosen_timeframe,
                    "template_name": item["strategy"].template_name,
                    "parameters": item["strategy"].parameters,
                    "exit_rule": item["strategy"].exit_rule,
                    "fast_period": item["strategy"].fast_period,
                    "slow_period": item["strategy"].slow_period,
                    "filters": item["strategy"].filters,
                    "stop_loss_pct": item["strategy"].stop_loss_pct,
                    "take_profit_pct": item["strategy"].take_profit_pct,
                    "position_fraction": item["strategy"].position_fraction,
                    "time_stop_bars": item["strategy"].time_stop_bars,
                    "trailing_atr_multiple": item["strategy"].trailing_atr_multiple,
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
            if any(info.get("stopped_candidates") for info in coverage.values()):
                reason += (
                    " Previously stopped strategy fingerprints were excluded; reset them "
                    "explicitly to retry."
                )
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
        regime = _regime(state)
        messages = [
            {
                "role": "system",
                "content": (
                    "Work in two steps, then submit one measured candidate. "
                    "Step 1: state the supplied market regime and why it fits. "
                    "Step 2: choose one strategy family from the library that has a reason to "
                    "work in that regime, and copy exactly one measured candidate. "
                    "Never invent a free-form EMA rule and never generate strategy code. "
                    "If the regime is choppy, reject a pure EMA crossover. "
                    "Explain the economic or statistical edge, not just the indicator rule. "
                    "Use long-horizon context for the directional bias and short-horizon "
                    "context only for timing. "
                    "eligible_candidates is the only set you may choose from. "
                    "Copy one row's template_name and chosen_timeframe. "
                    "Do not invent a template or timeframe outside that list. "
                    "Measured parameters, stops, and the exit rule are kept when your copy "
                    "differs. Prefer positive net-of-cost PnL, controlled drawdown, regime fit, "
                    "and a family that has not recently lost in this regime. "
                    "Call submit_strategy exactly once. On revision, choose a different "
                    "eligible row and apply the critique instructions."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": state.symbol,
                        "regime": regime,
                        "reference_examples": REFERENCE_EXAMPLES,
                        "family_memory": self.context.database.recent_family_results(
                            state.symbol, str(regime.get("regime") or ""), limit=8
                        ),
                        "external_context": _external_brief(state),
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
                        "Name one measured candidate by its template and timeframe. "
                        "Parameters are replaced with the tested row."
                    ),
                )
            ],
            self.context.settings.model_for("strategy"),
        )
        submitted = parse_tool_response(response, "submit_strategy", StrategyDefinition)
        strategy, match = _resolve_measured(submitted, candidates)
        if match != "exact":
            self.context.database.log_event(
                state.run_id,
                "strategy_selection_snapped",
                (
                    f"Submitted {submitted.template_name} on {submitted.chosen_timeframe} "
                    f"did not match a measured candidate exactly. "
                    f"Using {strategy.template_name} on {strategy.chosen_timeframe} ({match})."
                ),
            )
        if submitted.description.strip():
            strategy = strategy.model_copy(update={"description": submitted.description.strip()})
        diagnostics["selection_match"] = match
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
        logger.info(
            "Strategy selected (template=%s, timeframe=%s, match=%s, candidate=%s).",
            strategy.template_name,
            strategy.chosen_timeframe,
            match,
            diagnostics["selected_candidate"]["candidate_id"],
        )
        strategy_id = f"{state.run_id}-v{state.revision_count}"
        family = STRATEGY_LIBRARY[strategy.template_name]["family"]
        self.context.database.log_event(
            state.run_id,
            "strategy_selected",
            (
                f"family={family} template={strategy.template_name} "
                f"regime={regime.get('regime')} timeframe={strategy.chosen_timeframe}"
            ),
        )
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

            stopped_candidates: list[dict[str, str]] = []
            regime = _regime(state)
            for template_name, template in STRATEGY_LIBRARY.items():
                if timeframe not in template["supported_timeframes"]:
                    continue
                if not _family_fits(template, regime):
                    continue
                parameters = dict(template["default_parameters"])
                series = _extra_series(state, timeframe, template_name, parameters)
                if series is None:
                    continue
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
                    exit_rule=template["default_exit_rule"],
                    fast_period=fast,
                    slow_period=slow,
                    parameters=parameters,
                    stop_loss_pct=float(risk["stop_loss_pct"]),
                    take_profit_pct=risk["take_profit_pct"],
                    position_fraction=0.1,
                    time_stop_bars=template.get("default_time_stop_bars"),
                    trailing_atr_multiple=template.get("default_trailing_atr_multiple"),
                    filters=["volume_positive"] if template_name == "ema_crossover" else [],
                )
                stop_reason = self.context.database.strategy_stop_reason(
                    state.symbol, strategy_fingerprint(strategy)
                )
                if stop_reason:
                    stopped_candidates.append(
                        {"template_name": template_name, "reason": stop_reason}
                    )
                    continue
                capital = (
                    state.account_equity
                    if state.account_equity is not None
                    else self.context.settings.initial_capital
                )
                report = self.context.backtester.run(
                    strategy, candles, data=series, initial_capital=capital
                )
                if report.total_trades >= self.context.settings.min_backtest_trades:
                    candidates.append(
                        {
                            "strategy": strategy,
                            "trades": report.total_trades,
                            "net_pnl": report.net_pnl,
                            "max_drawdown": report.max_drawdown_pct,
                        }
                    )
            if stopped_candidates:
                info["stopped_candidates"] = stopped_candidates
        return candidates, coverage


def _resolve_measured(
    submitted: StrategyDefinition, candidates: list[dict[str, object]]
) -> tuple[StrategyDefinition, str]:
    """Return the tested strategy the submission refers to.

    Each template is measured once per timeframe. Models often change a parameter,
    filter, or stop while naming that template, so the measured row is the strategy
    that actually gets traded.
    """
    exact = [
        item for item in candidates if _strategy_key(item["strategy"]) == _strategy_key(submitted)
    ]
    if exact:
        return exact[0]["strategy"], "exact"
    same_slot = [
        item
        for item in candidates
        if item["strategy"].template_name == submitted.template_name
        and item["strategy"].chosen_timeframe == submitted.chosen_timeframe
    ]
    if same_slot:
        return _best(same_slot), "template_timeframe"
    same_template = [
        item for item in candidates if item["strategy"].template_name == submitted.template_name
    ]
    if same_template:
        return _best(same_template), "template"
    family = STRATEGY_LIBRARY.get(submitted.template_name, {}).get("family")
    if family:
        same_family = [
            item
            for item in candidates
            if STRATEGY_LIBRARY[item["strategy"].template_name]["family"] == family
        ]
        if same_family:
            return _best(same_family), "family"
    return _best(candidates), "best_measured"


def _best(candidates: list[dict[str, object]]) -> StrategyDefinition:
    return max(candidates, key=lambda item: item["net_pnl"])["strategy"]


def _strategy_key(
    strategy: StrategyDefinition,
) -> tuple[
    str,
    str,
    int,
    int,
    str,
    tuple[str, ...],
    float,
    float | None,
    float,
    str,
    int | None,
    float | None,
]:
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
        strategy.exit_rule,
        strategy.time_stop_bars,
        strategy.trailing_atr_multiple,
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


def _regime(state: GraphState) -> dict[str, object]:
    if state.regime:
        return state.regime
    if not state.raw_data:
        return {}
    return diagnose_regime(state.raw_data, state.market_context)


def _family_fits(template: dict[str, object], regime: dict[str, object]) -> bool:
    label = str(regime.get("regime") or "")
    try:
        trend_strength = float(regime.get("trend_strength") or 0)
    except (TypeError, ValueError):
        trend_strength = 0.0
    choppy = label.startswith("choppy") or (bool(label) and trend_strength < 0.35)
    if template.get("pure_momentum") and choppy:
        return False
    suitable = template.get("suitable_regimes")
    if label and isinstance(suitable, list) and label not in suitable:
        return False
    return True


def _extra_series(
    state: GraphState,
    timeframe: str,
    template_name: str,
    parameters: dict[str, object],
) -> dict[str, object] | None:
    series: dict[str, object] = dict(state.raw_data)
    if template_name == "stat_arb_spread":
        pair = (state.market_context.get("pair_candles") or {}).get(timeframe)
        if not pair:
            return None
        pair_symbol = state.market_context.get("pair_symbol") or parameters.get("pair_symbol")
        if isinstance(pair_symbol, str):
            parameters["pair_symbol"] = pair_symbol
        series["pair"] = pair
    if template_name == "carry_funding":
        funding = state.market_context.get("funding_history")
        if not funding:
            return None
        series["funding"] = funding
    return series


def _external_brief(state: GraphState) -> dict[str, object]:
    external = state.market_context.get("external") or {}
    if not isinstance(external, dict):
        return {}
    return {
        "long_horizon": external.get("long_horizon") or [],
        "short_horizon": external.get("short_horizon") or [],
    }


def _timeframe_rank(timeframe: str) -> int:
    units = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
    try:
        return int(timeframe[:-1]) * units[timeframe[-1]]
    except (KeyError, ValueError):
        return 0
