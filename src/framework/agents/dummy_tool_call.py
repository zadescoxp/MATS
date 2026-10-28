from pydantic import BaseModel, Field

from framework.agents.common import parse_tool_response, result_with_response, submit_tool
from framework.core.context import AgentContext
from framework.core.recovery import AgentResult
from framework.core.state import GraphState


class EchoResult(BaseModel):
    message: str = Field(min_length=1)


class DummyToolCallingNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> AgentResult:
        response = self.context.llm.invoke_with_tools(
            [
                {"role": "system", "content": "Call echo exactly once with the supplied message."},
                {"role": "user", "content": "Return the message 'tool calling is ready'."},
            ],
            [submit_tool("echo", EchoResult, "Return a short readiness message.")],
            self.context.settings.model_for("dummy"),
        )
        parse_tool_response(response, "echo", EchoResult)
        history = [
            *state.messages,
            {"role": "assistant", "content": response.content, "tool": "echo"},
        ]
        return result_with_response({"messages": history}, response, history)
