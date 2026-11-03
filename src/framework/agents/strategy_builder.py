import copy
import json
import logging
import pickle
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from time import perf_counter

from framework.agents.common import invoke_structured, result_with_response, submit_tool
from framework.config.settings import Settings
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import StrategyChoice, StrategyDefinition
from framework.strategies.library import (
    REFERENCE_EXAMPLES,
    STRATEGY_LIBRARY,
    strategy_fingerprint,
    strategy_library_summary,
)
from framework.strategies.regime import diagnose_regime
from framework.strategies.search import TemplateJob, measure_template
from framework.tools.markets import MarketType, is_perpetual, market_type

_MAX_CANDIDATES_PER_TIMEFRAME = 5
# Measurements reused by later attempts of the same run on the same candles.
_MEASURED: dict[tuple, tuple[list[dict[str, object]], dict[str, dict[str, object]]]] = {}
_MEASURED_LOCK = threading.Lock()
_MEASURED_LIMIT = 16
logger = logging.getLogger(__name__)


class StrategyBuilderNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> AgentResult:
        if not state.analyst_findings:
            raise ValueError("Strategy builder requires analyst findings")

        candidates, coverage = self._candidate_strategies(state)
        previous_key = _strategy_key(state.strategy) if state.strategy else None
        tried = {
            _strategy_key(StrategyDefinition.model_validate(definition))
            for definition in self.context.database.run_strategies(
                state.run_id, state.revision_count
            )
        }
        if previous_key:
            tried.add(previous_key)
        candidates = [item for item in candidates if _strategy_key(item["strategy"]) not in tried]
        candidates = _shortlist(candidates, most_active=state.test_mode)
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
                    "measured_on": item["measured_on"],
                    "closed_trades": item["trades"],
                    "net_pnl_after_costs": item["net_pnl"],
                    "max_drawdown_pct": item["max_drawdown"],
                    "cost_edge_ratio": item["in_sample"]["cost_edge_ratio"],
                    "win_rate": item["in_sample"]["win_rate"],
                    "variants_tested": item["variants_tested"],
                }
                for item in candidates
            ],
        }
        if not candidates:
            detail = _no_candidate_detail(
                coverage,
                self.context.settings.min_strategy_bars,
                self.context.settings.min_backtest_trades,
            )
            no_edge = bool(tried) or any(
                info.get("no_in_sample_edge") for info in coverage.values()
            )
            if no_edge:
                # Not an error: nothing measured earned more than it paid in costs.
                reason = (
                    f"Staying in cash: no strategy showed an edge after fees and slippage. {detail}"
                )
            else:
                reason = f"No untried library/timeframe candidate could be tested. {detail}"
            if tried:
                reason += " All candidates with an in-sample edge were already attempted."
            if any(info.get("stopped_candidates") for info in coverage.values()):
                reason += (
                    " Previously stopped strategy fingerprints were excluded; reset them "
                    "explicitly to retry."
                )
            return AgentResult(
                updates={
                    "strategy": None,
                    "strategy_diagnostics": diagnostics,
                    "status": "rejected" if no_edge else "failed",
                    "errors": [*state.errors, reason],
                }
            )

        instructions = (
            state.critique.revision_instructions
            if state.critique and state.critique.decision == "REJECT"
            else "Choose the best evidence-supported candidate."
        )
        if state.test_mode:
            instructions = (
                "Testnet test mode: the goal is frequent trades to exercise order handling, "
                "not profit. Choose the candidate with the most closed trades."
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
                    "Submit that row's candidate_id. "
                    "Do not invent a template or timeframe outside that list. "
                    "Measured parameters, stops, and the exit rule are kept when your copy "
                    "differs. Each row is the best of several parameter and risk variants, "
                    "measured on the in-sample history only; the rest of the history is held "
                    "out and the critique judges the choice on it, so favor robust edges over "
                    "the single best number. Prefer a high cost_edge_ratio (gross profit per "
                    "unit of fees and slippage), fewer longer trades, controlled drawdown, "
                    "regime fit, and a family that has not recently lost in this regime. "
                    "Call submit_strategy exactly once. On revision, choose a different "
                    "eligible row and apply the critique instructions. "
                    "market says what the run trades: spot strategies only go long and sit "
                    "in cash on bearish signals; perpetual strategies with short_entries also "
                    "go short on bearish signals, so a falling market can be an opportunity."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": state.symbol,
                        "market": {
                            "type": market_type(state.symbol),
                            "short_entries": is_perpetual(state.symbol)
                            and self.context.settings.allow_short,
                        },
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
        submitted, response = invoke_structured(
            self.context.llm,
            messages,
            submit_tool(
                "submit_strategy",
                StrategyChoice,
                (
                    "Pick one eligible candidate by candidate_id and describe its edge. "
                    "The tested parameters of that row are used."
                ),
            ),
            StrategyChoice,
            self.context.settings.model_for("strategy"),
        )
        strategy, match = _resolve_choice(submitted, candidates)
        if match != "exact":
            self.context.database.log_event(
                state.run_id,
                "strategy_selection_snapped",
                (
                    f"Submitted {submitted.candidate_id or submitted.template_name} on "
                    f"{submitted.chosen_timeframe or 'any timeframe'} "
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
        settings = self.context.settings
        capital = (
            state.account_equity if state.account_equity is not None else settings.initial_capital
        )
        key = _measurement_key(state, capital, settings)
        with _MEASURED_LOCK:
            cached = _MEASURED.get(key)
        if cached is None:
            cached = self._measure(state, capital)
            with _MEASURED_LOCK:
                _MEASURED[key] = cached
                while len(_MEASURED) > _MEASURED_LIMIT:
                    _MEASURED.pop(next(iter(_MEASURED)))
        measured, coverage = copy.deepcopy(cached)
        candidates: list[dict[str, object]] = []
        for item in measured:
            strategy = item["strategy"]
            info = coverage[strategy.chosen_timeframe]
            stop_reason = self.context.database.strategy_stop_reason(
                state.symbol, strategy_fingerprint(strategy)
            )
            if stop_reason:
                info.setdefault("stopped_candidates", []).append(
                    {"template_name": strategy.template_name, "reason": stop_reason}
                )
                continue
            # Test mode trades without an edge, to exercise orders on the testnet.
            if not state.test_mode and not _has_edge(item, settings.min_cost_edge_ratio):
                info.setdefault("no_in_sample_edge", []).append(
                    {
                        "template_name": strategy.template_name,
                        "net_pnl": item["net_pnl"],
                        "cost_edge_ratio": item["in_sample"]["cost_edge_ratio"],
                    }
                )
                continue
            candidates.append(item)
        return candidates, coverage

    def _measure(
        self, state: GraphState, capital: float
    ) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
        """Search every offerable template's variants on each timeframe's in-sample bars."""
        settings = self.context.settings
        coverage: dict[str, dict[str, object]] = {}
        jobs: list[tuple[str, str, TemplateJob]] = []
        regime = _regime(state)
        for timeframe, candles in state.raw_data.items():
            bar_count = len(candles)
            info: dict[str, object] = {
                "bars": bar_count,
                "first_timestamp": candles[0].timestamp.isoformat() if candles else None,
                "last_timestamp": candles[-1].timestamp.isoformat() if candles else None,
                "eligible": bar_count >= settings.min_strategy_bars,
            }
            coverage[timeframe] = info
            if bar_count < settings.min_strategy_bars:
                info["reason"] = "insufficient_history"
                continue
            # Why each template was not offered on this timeframe, for the run diagnosis.
            skipped: dict[str, list[str]] = {}
            for template_name, template in STRATEGY_LIBRARY.items():
                if timeframe not in template["supported_timeframes"]:
                    skipped.setdefault("timeframe_not_supported", []).append(template_name)
                    continue
                if not _family_fits(template, regime):
                    skipped.setdefault("does_not_fit_regime", []).append(template_name)
                    continue
                entries: list[StrategyDefinition] = []
                series: dict[str, object] | None = None
                reason: str | None = None
                for variant in [{}, *template["parameter_variants"]]:
                    parameters = {**template["default_parameters"], **variant}
                    series = strategy_series(state, timeframe, template_name, parameters)
                    if series is None:
                        reason = "missing_pair_or_funding_data"
                        break
                    if parameters.get("higher_timeframe"):
                        available_higher = [
                            item
                            for item in state.timeframes_to_consider
                            if item in state.raw_data
                            and _timeframe_rank(item) > _timeframe_rank(timeframe)
                        ]
                        if not available_higher:
                            reason = "needs_higher_timeframe"
                            break
                        parameters["higher_timeframe"] = min(available_higher, key=_timeframe_rank)
                    entries.append(
                        _template_strategy(
                            template_name,
                            timeframe,
                            parameters,
                            market=market_type(state.symbol),
                            short_entries=is_perpetual(state.symbol) and settings.allow_short,
                        )
                    )
                if reason or series is None:
                    skipped.setdefault(reason or "missing_pair_or_funding_data", []).append(
                        template_name
                    )
                    continue
                jobs.append(
                    (
                        timeframe,
                        template_name,
                        TemplateJob(
                            backtester=self.context.backtester,
                            strategies=entries,
                            candles=list(candles),
                            series=_signal_series(series, timeframe, entries),
                            capital=capital,
                            min_trades=settings.min_backtest_trades,
                        ),
                    )
                )
            if skipped:
                info["skipped_templates"] = skipped

        started_at = perf_counter()
        results = _run_jobs([job for _, _, job in jobs], settings.backtest_workers)
        measured: list[dict[str, object]] = []
        variants = 0
        for (timeframe, template_name, _), result in zip(jobs, results, strict=True):
            variants += int(result["variants_tested"])
            if result["strategy"] is None:
                coverage[timeframe].setdefault("too_few_trades", []).append(
                    {"template_name": template_name, "closed_trades": result["closed_trades"]}
                )
                continue
            measured.append(result)
        logger.info(
            "Measured strategy variants (templates=%d, variants=%d, duration=%.1fs).",
            len(jobs),
            variants,
            perf_counter() - started_at,
        )
        return measured, coverage


def _template_strategy(
    template_name: str,
    timeframe: str,
    parameters: dict[str, object],
    *,
    market: MarketType = "spot",
    short_entries: bool = False,
) -> StrategyDefinition:
    template = STRATEGY_LIBRARY[template_name]
    risk = template["default_risk"]
    return StrategyDefinition(
        name=f"{timeframe} {template['name']}",
        description=str(template["description"]),
        chosen_timeframe=timeframe,
        template_name=template_name,
        entry_rule=template_name,
        exit_rule=template["default_exit_rule"],
        fast_period=int(parameters.get("fast_period", parameters.get("macd_fast", 12))),
        slow_period=int(parameters.get("slow_period", parameters.get("macd_slow", 26))),
        parameters=parameters,
        stop_loss_pct=float(risk["stop_loss_pct"]),
        take_profit_pct=risk["take_profit_pct"],
        position_fraction=0.1,
        time_stop_bars=template.get("default_time_stop_bars"),
        trailing_atr_multiple=template.get("default_trailing_atr_multiple"),
        filters=["volume_positive"] if template_name == "ema_crossover" else [],
        market=market,
        short_entries=short_entries,
    )


def _signal_series(
    series: dict[str, object], timeframe: str, entries: list[StrategyDefinition]
) -> dict[str, object]:
    """Only the series these strategies read, so parallel jobs copy less data."""
    wanted = {timeframe, "pair", "funding"} | {
        str(entry.parameters["higher_timeframe"])
        for entry in entries
        if entry.parameters.get("higher_timeframe")
    }
    return {name: value for name, value in series.items() if name in wanted}


def _run_jobs(jobs: list[TemplateJob], workers: int) -> list[dict[str, object]]:
    if workers > 1 and len(jobs) > 1:
        try:
            with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
                return list(pool.map(measure_template, jobs))
        except (BrokenProcessPool, OSError, pickle.PicklingError):
            logger.warning("Parallel measurement failed; measuring in-process.", exc_info=True)
    return [measure_template(job) for job in jobs]


def _has_edge(item: dict[str, object], min_cost_edge_ratio: float) -> bool:
    """In-sample profit after costs, with gross profit well above what costs took."""
    if float(item["net_pnl"]) <= 0:
        return False
    ratio = item["in_sample"].get("cost_edge_ratio")
    return ratio is None or ratio >= min_cost_edge_ratio


def _measurement_key(state: GraphState, capital: float, settings: Settings) -> tuple:
    return (
        state.run_id,
        state.symbol,
        capital,
        str(state.regime.get("regime") or ""),
        tuple(
            (timeframe, len(candles), candles[-1].timestamp.isoformat() if candles else "")
            for timeframe, candles in sorted(state.raw_data.items())
        ),
        settings.walk_forward_split,
        settings.min_backtest_trades,
        settings.min_strategy_bars,
        settings.allow_short,
    )


_SKIP_LABELS = {
    "timeframe_not_supported": "templates do not trade {timeframe}",
    "needs_higher_timeframe": "need a timeframe above {timeframe} in the run",
    "does_not_fit_regime": "do not fit the current regime",
    "missing_pair_or_funding_data": "lack pair or funding data",
}


def _no_candidate_detail(
    coverage: dict[str, dict[str, object]], min_bars: int, min_trades: int
) -> str:
    """One sentence per timeframe naming why nothing could be offered there."""
    parts: list[str] = []
    for timeframe, info in sorted(coverage.items(), key=lambda item: _timeframe_rank(item[0])):
        if info.get("reason") == "insufficient_history":
            parts.append(f"{timeframe}: only {info.get('bars')} bars (need {min_bars}).")
            continue
        skipped = info.get("skipped_templates") or {}
        few = info.get("too_few_trades") or []
        losing = info.get("no_in_sample_edge") or []
        if not few and not losing and list(skipped) == ["timeframe_not_supported"]:
            parts.append(f"{timeframe}: no strategy template trades this timeframe.")
            continue
        notes = [
            f"{len(names)} {_SKIP_LABELS.get(key, key).format(timeframe=timeframe)}"
            for key, names in skipped.items()
            if names
        ]
        if few:
            tested = ", ".join(f"{item['template_name']} {item['closed_trades']}" for item in few)
            notes.append(f"too few closed trades ({tested}; need {min_trades})")
        if losing:
            measured = ", ".join(
                f"{item['template_name']} {float(item['net_pnl']):+.0f}" for item in losing
            )
            notes.append(f"no in-sample edge after costs ({measured})")
        parts.append(f"{timeframe}: {'; '.join(notes) or 'no candidates'}.")
    return " ".join(parts)


def _resolve_choice(
    choice: StrategyChoice, candidates: list[dict[str, object]]
) -> tuple[StrategyDefinition, str]:
    by_id = next(
        (item for item in candidates if item.get("candidate_id") == choice.candidate_id), None
    )
    if by_id is not None:
        return by_id["strategy"], "exact"
    named = next(
        (
            item["strategy"]
            for item in candidates
            if item["strategy"].template_name == choice.template_name
        ),
        None,
    )
    if named is None:
        return _best(candidates), "best_measured"
    reference = named.model_copy(
        update={"chosen_timeframe": choice.chosen_timeframe or named.chosen_timeframe}
    )
    return _resolve_measured(reference, candidates)


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


def _shortlist(
    candidates: list[dict[str, object]], *, most_active: bool = False
) -> list[dict[str, object]]:
    """The best candidates per timeframe; in test mode, the ones that trade most often."""
    ordered = sorted(
        candidates,
        key=lambda item: (
            (item["trades"], item["net_pnl"], -item["max_drawdown"])
            if most_active
            else (item["net_pnl"], -item["max_drawdown"], item["trades"])
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


def strategy_series(
    state: GraphState,
    timeframe: str,
    template_name: str,
    parameters: dict[str, object],
) -> dict[str, object] | None:
    """Every series a template reads: all timeframes, plus its reference pair or funding."""
    series: dict[str, object] = dict(state.raw_data)
    if template_name == "stat_arb_spread":
        pair = (state.market_context.get("pair_candles") or {}).get(timeframe)
        if not pair:
            return None
        pair_symbol = state.market_context.get("pair_symbol") or parameters.get("pair_symbol")
        if isinstance(pair_symbol, str):
            parameters["pair_symbol"] = pair_symbol
        series["pair"] = pair
    funding = state.market_context.get("funding_history")
    if template_name == "carry_funding" and not funding:
        return None
    # Carry reads funding as its signal; perpetual positions also pay it in the backtest.
    if funding and (template_name == "carry_funding" or is_perpetual(state.symbol)):
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
