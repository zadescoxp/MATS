import json

from framework.agents.common import parse_tool_response, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState
from framework.schemas.models import MultiTimeframeFindings
from framework.tools.technicals import compute_multi_timeframe


class MultiTimeframeAnalystNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> AgentResult:
        if not state.raw_data:
            raise ValueError("Analyst requires OHLCV data for at least one timeframe")
        snapshots = compute_multi_timeframe(state.raw_data)
        snapshot_payload = {
            timeframe: snapshot.model_dump(mode="json") for timeframe, snapshot in snapshots.items()
        }
        timeframe_order = sorted(state.raw_data, key=_timeframe_seconds)
        highest, lowest = timeframe_order[-1], timeframe_order[0]
        messages = [
            {
                "role": "system",
                "content": (
                    "Analyze all supplied timeframes together. Assess alignment, divergence, "
                    "regime, momentum, volatility, and key levels. Do not propose a strategy. "
                    "Call submit_findings exactly once with the complete structured finding."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "symbol": state.symbol,
                        "higher_timeframe": highest,
                        "lower_timeframe": lowest,
                        "technical_snapshots": snapshot_payload,
                        "market_context": state.market_context,
                    },
                    separators=(",", ":"),
                ),
            },
        ]
        tool = submit_tool(
            "submit_findings",
            MultiTimeframeFindings,
            "Submit one combined multi-timeframe market finding.",
        )
        response = self.context.llm.invoke_with_tools(
            messages, [tool], self.context.settings.model_for("analyst")
        )
        finding = parse_tool_response(response, "submit_findings", MultiTimeframeFindings)
        if finding.higher_timeframe != highest or finding.lower_timeframe != lowest:
            raise ValueError("Analyst findings must use the highest and lowest supplied timeframes")
        finding = finding.model_copy(update={"technical_snapshots": snapshot_payload})
        history = [
            *state.messages,
            {"role": "assistant", "content": response.content, "tool": "submit_findings"},
        ]
        return result_with_response({"analyst_findings": [finding]}, response, history)


def _timeframe_seconds(timeframe: str) -> int:
    number = int(timeframe[:-1])
    unit = timeframe[-1]
    factors = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
    if unit not in factors:
        raise ValueError(f"Unsupported timeframe {timeframe!r}")
    return number * factors[unit]
