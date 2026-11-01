import json
import logging

from framework.agents.common import parse_tool_response, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import Critique
from framework.strategies.library import STRATEGY_LIBRARY

logger = logging.getLogger(__name__)


class CritiqueNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> AgentResult:
        report = state.backtest_report
        if report is None:
            raise ValueError("Critique requires a completed backtest report")
        gate_issues = self._gate_issues(state)
        review_metrics = {
            "net_pnl_after_costs": report.net_pnl,
            "net_return_after_costs": report.net_return_pct,
            "max_drawdown": report.max_drawdown_pct,
            "sharpe_ratio": report.sharpe_ratio,
            "win_rate": report.win_rate,
            "total_trades": report.total_trades,
            "total_fees": report.total_fees,
            "total_slippage": report.total_slippage,
            "required_thresholds": {
                "minimum_net_pnl": self.context.settings.min_backtest_net_pnl,
                "minimum_trades": self.context.settings.min_backtest_trades,
                "maximum_drawdown": self.context.settings.max_backtest_drawdown,
            },
        }
        family = _family(state)
        messages = [
            {
                "role": "system",
                "content": (
                    "Evaluate the net-of-cost backtest, then score two extra dimensions from "
                    "0 to 1: diversity_score and regime_fit_score. "
                    "diversity_score is low when this is another trend-following variant, "
                    "especially a pure EMA crossover, or when it repeats a family that already "
                    "failed in this regime. regime_fit_score is low when the family has no "
                    "reason to work in the supplied regime. "
                    "State the edge hypothesis in edge_hypothesis. Ask yourself: is this just "
                    "another trend-following variant, what is the actual edge, and how does it "
                    "fail in the opposite regime? "
                    "Never approve when any listed threshold fails. A pure EMA crossover "
                    "without a regime filter is a reject in chop. "
                    "On rejection, provide specific revisions and call submit_critique once."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "metrics": review_metrics,
                        "deterministic_issues": gate_issues,
                        "strategy_family": family,
                        "template_name": state.strategy.template_name if state.strategy else None,
                        "regime": state.regime,
                        "family_memory": self.context.database.recent_family_results(
                            state.symbol,
                            str(state.regime.get("regime") or ""),
                            limit=8,
                        ),
                    },
                    separators=(",", ":"),
                ),
            },
        ]
        response = self.context.llm.invoke_with_tools(
            messages,
            [submit_tool("submit_critique", Critique, "Submit the backtest critique.")],
            self.context.settings.model_for("critic"),
        )
        proposed = parse_tool_response(response, "submit_critique", Critique)
        if gate_issues:
            proposed = proposed.model_copy(
                update={
                    "decision": "REJECT",
                    "issues": list(dict.fromkeys([*proposed.issues, *gate_issues])),
                    "revision_instructions": (
                        proposed.revision_instructions.strip() + " "
                        if proposed.revision_instructions.strip()
                        else ""
                    )
                    + "Revise the strategy to satisfy: "
                    + "; ".join(gate_issues),
                }
            )
        if proposed.decision == "REJECT" and not proposed.revision_instructions.strip():
            action = (
                "; ".join(proposed.issues)
                if proposed.issues
                else "improve net-of-cost returns and control drawdown"
            )
            proposed = proposed.model_copy(
                update={
                    "revision_instructions": (
                        "Reassess the strategy family against the regime and change the "
                        f"template, filter, or exit so it addresses: {action}."
                    )
                }
            )
        critique = Critique.model_validate(proposed)
        if state.strategy is not None:
            self.context.database.record_family_result(
                symbol=state.symbol,
                family=family,
                template_name=state.strategy.template_name,
                regime=str(state.regime.get("regime") or "unknown"),
                net_pnl=report.net_pnl,
                decision=critique.decision,
            )
        critique_id = f"{state.run_id}-v{state.revision_count}"
        self.context.database.save_critique(
            critique_id,
            critique_id,
            critique.decision,
            critique.issues,
            critique.revision_instructions,
        )
        logger.info(
            "Strategy critique completed (decision=%s, issues=%d, diversity=%.2f, "
            "regime_fit=%.2f).",
            critique.decision,
            len(critique.issues),
            critique.diversity_score,
            critique.regime_fit_score,
        )
        updates: dict[str, object] = {"critique": critique}
        if critique.decision == "REJECT":
            limit = (
                state.max_revision_loops
                if state.max_revision_loops is not None
                else self.context.settings.max_revision_loops
            )
            if state.revision_count >= limit:
                updates.update(
                    status="rejected",
                    errors=[
                        *state.errors,
                        f"Strategy attempt limit reached ({state.revision_count + 1} total "
                        "attempts); strategy was not approved.",
                    ],
                )
            else:
                updates["revision_count"] = state.revision_count + 1
        history = [
            *state.messages,
            {"role": "assistant", "content": response.content, "tool": "submit_critique"},
        ]
        return result_with_response(updates, response, history)

    def _gate_issues(self, state: GraphState) -> list[str]:
        report = state.backtest_report
        if report is None:
            raise ValueError("Critique requires a completed backtest report")
        issues: list[str] = []
        if report.net_pnl <= self.context.settings.min_backtest_net_pnl:
            issues.append(
                f"Net PnL after fees and slippage must exceed "
                f"{self.context.settings.min_backtest_net_pnl:.2f}."
            )
        if report.total_trades < self.context.settings.min_backtest_trades:
            issues.append(
                f"At least {self.context.settings.min_backtest_trades} closed trades are required."
            )
        if report.max_drawdown_pct > self.context.settings.max_backtest_drawdown:
            issues.append(
                f"Maximum drawdown must not exceed "
                f"{self.context.settings.max_backtest_drawdown:.1%}."
            )
        strategy = state.strategy
        if strategy is not None and strategy.template_name == "ema_crossover":
            label = str(state.regime.get("regime") or "")
            try:
                trend_strength = float(state.regime.get("trend_strength") or 0)
            except (TypeError, ValueError):
                trend_strength = 0.0
            choppy = label.startswith("choppy") or trend_strength < 0.35 or not label
            if choppy:
                issues.append(
                    "Pure EMA crossover without a regime filter or extra confirmation "
                    "is rejected in a choppy or unknown regime."
                )
        family = _family(state)
        if family == "momentum" and str(state.regime.get("regime") or "").startswith("choppy"):
            issues.append(
                "Momentum family does not fit a choppy regime. Pick mean-reversion, "
                "relative value, or a volatility fade."
            )
        regime_label = str(state.regime.get("regime") or "")
        if regime_label:
            losses = [
                row
                for row in self.context.database.recent_family_results(
                    state.symbol, regime_label, limit=8
                )
                if row["family"] == family and float(row["net_pnl"]) <= 0
            ]
            if len(losses) >= 2:
                issues.append(
                    f"{family} already lost in {regime_label} on recent runs. "
                    "Choose a different family."
                )
        return issues


def _family(state: GraphState) -> str:
    if state.strategy is None:
        return "unknown"
    template = STRATEGY_LIBRARY.get(state.strategy.template_name)
    if template is None:
        return state.strategy.template_name
    return str(template["family"])
