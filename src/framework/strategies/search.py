"""Parameter search for one strategy template, judged on in-sample history only.

The builder measures every template it may offer this way. Signals depend only on the
entry parameters, so each entry variant is signalled once and its risk variants (stop,
target, time stop) are simulated on those same signals. Variants are ranked on the first
part of the history; the remainder stays unseen until the critique's out-of-sample check.

Only after the winner is chosen is it also simulated on the unseen part. That outcome is
never used to choose; it records how good the choice was (see ``framework.strategies.scoring``).
"""

from dataclasses import dataclass
from typing import Any

from framework.schemas.models import OHLCVBar, StrategyDefinition
from framework.strategies.scoring import (
    outcome_score,
    selection_score,
    t_statistic,
    trade_returns,
)
from framework.tools.backtest import BacktestTool, segment_summary


@dataclass
class TemplateJob:
    backtester: BacktestTool
    strategies: list[StrategyDefinition]
    candles: list[OHLCVBar]
    series: dict[str, Any]
    capital: float
    min_trades: int
    # "edge" ranks variants by selection_score; "net_pnl" by profit, then drawdown.
    ranking: str = "net_pnl"
    search_weight: float = 0.5
    overfit_weight: float = 0.5


def risk_variants(strategy: StrategyDefinition) -> list[dict[str, Any]]:
    """Stop, target, and time-stop alternatives; wider ones trade less and pay less cost."""
    stop = strategy.stop_loss_pct
    target = strategy.take_profit_pct
    if target:
        pairs = [(stop, target), (stop * 1.5, target * 1.5), (stop * 2, target * 2)]
        pairs.append((stop, target * 2))
    else:
        pairs = [(stop, None), (stop * 2, None)]
    variants = [
        {"stop_loss_pct": round(min(s, 0.5), 6), "take_profit_pct": t and round(min(t, 1), 6)}
        for s, t in pairs
    ]
    if strategy.time_stop_bars:
        variants.append({**variants[0], "time_stop_bars": strategy.time_stop_bars * 2})
    return variants


def measure_template(job: TemplateJob) -> dict[str, Any]:
    """Best in-sample variant of one template, or how many trades the busiest one made."""
    tool = job.backtester
    split = tool.split_index(len(job.candles))
    end = split if split is not None else len(job.candles)
    in_sample = job.candles[:end]
    funding = job.series.get("funding")
    best: tuple[tuple[float, float], StrategyDefinition, Any, list[Any]] | None = None
    most_trades = 0
    tested = 0
    for entry in job.strategies:
        every_signal = tool.signals(entry, job.candles, data=job.series)
        signals = every_signal[:end]
        for update in risk_variants(entry):
            strategy = entry.model_copy(update=update)
            report = tool.simulate(strategy, in_sample, signals, job.capital, funding=funding)
            tested += 1
            most_trades = max(most_trades, report.total_trades)
            if report.total_trades < job.min_trades:
                continue
            if job.ranking == "edge":
                # The search penalty is the same for every variant of a template.
                primary = t_statistic(trade_returns(report.trades))
            else:
                primary = report.net_pnl
            score = (primary, -report.max_drawdown_pct)
            if best is None or score > best[0]:
                best = (score, strategy, report, every_signal)
    if best is None:
        return {"strategy": None, "closed_trades": most_trades, "variants_tested": tested}
    _, strategy, report, every_signal = best
    result: dict[str, Any] = {
        "strategy": strategy,
        "trades": report.total_trades,
        "net_pnl": report.net_pnl,
        "max_drawdown": report.max_drawdown_pct,
        "in_sample": segment_summary(report, in_sample),
        "measured_on": "in_sample" if split is not None else "full_history",
        "variants_tested": tested,
        "selection_score": selection_score(report.trades, tested, job.search_weight),
    }
    if split is not None:
        # The choice is made; now see how it does on history it never saw.
        unseen = tool.simulate(
            strategy, job.candles[split:], every_signal[split:], job.capital, funding=funding
        )
        result["unseen"] = segment_summary(unseen, job.candles[split:])
        result["outcome_score"] = outcome_score(
            report.trades,
            unseen.trades,
            tested,
            overfit_weight=job.overfit_weight,
            search_weight=job.search_weight,
        )
    return result
