from typing import Any, TypeVar

from pydantic import BaseModel

from framework.core.recovery import AgentResult
from framework.llm.client import LLMResponse, ToolDefinition

ModelT = TypeVar("ModelT", bound=BaseModel)


def submit_tool(name: str, model: type[ModelT], description: str) -> ToolDefinition:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return ToolDefinition(name=name, description=description, parameters=schema)


def parse_tool_response(response: LLMResponse, tool_name: str, model: type[ModelT]) -> ModelT:
    matches = [call for call in response.tool_calls if call.name == tool_name]
    if not matches:
        raise ValueError(f"Expected a {tool_name!r} tool call; received none")
    arguments = matches[0].arguments
    if any(call.arguments != arguments for call in matches[1:]):
        raise ValueError(
            f"Model returned conflicting {tool_name!r} tool calls; refusing ambiguous output"
        )
    return model.model_validate(arguments)


def result_with_response(
    updates: dict[str, Any], response: LLMResponse, messages: list[dict[str, Any]]
) -> AgentResult:
    return AgentResult(
        updates={**updates, "messages": messages},
        tokens_used=response.tokens_used,
        cost=response.cost,
        tool_calls=[
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in response.tool_calls
        ],
    )
