import json

from pydantic import BaseModel, Field

from framework.agents.common import invoke_structured, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState


class GeneratedCode(BaseModel):
    python_code: str = Field(min_length=1)


class CoderNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> AgentResult:
        if not state.critique or state.critique.decision != "APPROVE":
            raise ValueError("Coder runs only after Critique approval")
        if not state.strategy or not state.risk_assessment:
            raise ValueError("Coder requires an approved strategy and risk plan")
        messages = [
            {
                "role": "system",
                "content": (
                    "Generate a Python signal-function skeleton from the approved strategy. "
                    "Do not include order placement, credentials, or side effects. Submit code "
                    "using submit_code exactly once."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "strategy": state.strategy.model_dump(mode="json"),
                        "risk_assessment": state.risk_assessment.model_dump(mode="json"),
                    },
                    separators=(",", ":"),
                ),
            },
        ]
        output, response = invoke_structured(
            self.context.llm,
            messages,
            submit_tool("submit_code", GeneratedCode, "Submit a Python signal function."),
            GeneratedCode,
            self.context.settings.model_for("coder"),
        )
        history = [
            *state.messages,
            {"role": "assistant", "content": response.content, "tool": "submit_code"},
        ]
        return result_with_response({"generated_code": output.python_code}, response, history)
