from typing import Any
from typing import TypeVar

from pydantic import BaseModel

from framework.core.recovery import AgentResult
from framework.llm.client import LLMResponse, ToolDefinition

ModelT = TypeVar("ModelT", bound=BaseModel)


def submit_tool(name: str, model: type[ModelT], description: str) -> ToolDefinition:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return ToolDefinition(name=name, description=description, parameters=schema)


def parse_tool_response(
    response: LLMResponse, tool_name: str, model: type[ModelT]
) -> ModelT:
    matches = [call for call in response.tool_calls if call.name == tool_name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {tool_name!r} tool call; received {len(matches)}")
    return model.model_validate(matches[0].arguments)


def result_with_response(
    updates: dict[str, Any], response: LLMResponse, messages: list[dict[str, Any]]
) -> AgentResult:
    return AgentResult(
        updates={**updates, "messages": messages},
        tokens_used=response.tokens_used,
        cost=response.cost,
    )
