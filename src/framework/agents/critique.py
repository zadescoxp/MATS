import json

from framework.agents.common import parse_tool_response, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import Critique


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
        messages = [
            {
                "role": "system",
                "content": (
                    "Evaluate only the supplied net-of-cost backtest metrics. Never approve "
                    "when any listed threshold fails. On rejection, provide specific, "
                    "actionable strategy revisions and call submit_critique exactly once."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"metrics": review_metrics, "deterministic_issues": gate_issues},
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
                    "revision_instructions": proposed.revision_instructions
                    or "Revise the strategy to satisfy: " + "; ".join(gate_issues),
                }
            )
        critique = Critique.model_validate(proposed)
        critique_id = f"{state.run_id}-v{state.revision_count}"
        self.context.database.save_critique(
            critique_id,
            critique_id,
            critique.decision,
            critique.issues,
            critique.revision_instructions,
        )

        updates: dict[str, object] = {"critique": critique}
        if critique.decision == "REJECT":
            if state.revision_count >= self.context.settings.max_revision_loops:
                updates.update(
                    status="failed",
                    errors=[
                        *state.errors,
                        "Critique revision limit reached; strategy was not approved.",
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
        return issues
