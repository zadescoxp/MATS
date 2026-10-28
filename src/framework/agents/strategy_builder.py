import json

from framework.agents.common import parse_tool_response, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import StrategyDefinition


class StrategyBuilderNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> AgentResult:
        if not state.analyst_findings:
            raise ValueError("Strategy builder requires analyst findings")
        instructions = (
            state.critique.revision_instructions
            if state.critique and state.critique.decision == "REJECT"
            else "Create a strategy appropriate to the evidence."
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Create a fully specified long-only spot strategy, choosing its timeframe "
                    "autonomously from the available data. Use the supported ema_crossover "
                    "entry and exit rules, state filters and stop logic, and call "
                    "submit_strategy exactly once. Incorporate all critique revision "
                    "instructions when revising."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": state.symbol,
                        "available_timeframes": list(state.raw_data),
                        "analyst_findings": [
                            item.model_dump(mode="json") for item in state.analyst_findings
                        ],
                        "previous_strategy": (
                            state.strategy.model_dump(mode="json") if state.strategy else None
                        ),
                        "critique_revision_instructions": instructions,
                        "revision_count": state.revision_count,
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
                    "Submit a complete, supported spot strategy definition.",
                )
            ],
            self.context.settings.model_for("strategy"),
        )
        strategy = parse_tool_response(response, "submit_strategy", StrategyDefinition)
        if strategy.chosen_timeframe not in state.raw_data:
            raise ValueError(
                f"Chosen timeframe {strategy.chosen_timeframe!r} has no historical data"
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
        return result_with_response({"strategy": strategy}, response, history)
