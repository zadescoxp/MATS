import ast
import json
from typing import Any, TypeVar

from pydantic import BaseModel

from framework.config.settings import ModelRoute
from framework.core.recovery import AgentResult
from framework.llm.client import LLMResponse, ToolDefinition

ModelT = TypeVar("ModelT", bound=BaseModel)


def submit_tool(name: str, model: type[ModelT], description: str) -> ToolDefinition:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return ToolDefinition(name=name, description=description, parameters=schema)


def invoke_structured(
    llm: Any,
    messages: list[dict[str, Any]],
    tool: ToolDefinition,
    model: type[ModelT],
    route: ModelRoute | str,
    *,
    repairs: int = 1,
) -> tuple[ModelT, LLMResponse]:
    """Call a model for one validated tool submission, letting it correct a bad reply.

    When the reply has no matching tool call or fails validation, the error is sent back
    and the model gets `repairs` more chances. Token counts and cost cover every call.
    """
    conversation = list(messages)
    totals = LLMResponse()
    for attempt in range(repairs + 1):
        response = llm.invoke_with_tools(conversation, [tool], route)
        totals = LLMResponse(
            content=response.content,
            tool_calls=response.tool_calls,
            prompt_tokens=totals.prompt_tokens + response.prompt_tokens,
            completion_tokens=totals.completion_tokens + response.completion_tokens,
            cost=totals.cost + response.cost,
        )
        try:
            return parse_tool_response(response, tool.name, model), totals
        except ValueError as exc:
            # Pydantic's ValidationError is a ValueError.
            if attempt == repairs:
                raise
            conversation = [
                *conversation,
                {"role": "assistant", "content": response.content or ""},
                {
                    "role": "user",
                    "content": (
                        f"Your {tool.name} call was invalid: {_short_error(exc)} "
                        f"Call {tool.name} once more with corrected arguments that match its "
                        "schema exactly. Lists must be JSON arrays, not strings."
                    ),
                },
            ]
    raise AssertionError("unreachable")


def _short_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:800]


def parse_tool_response(response: LLMResponse, tool_name: str, model: type[ModelT]) -> ModelT:
    matches = [call for call in response.tool_calls if call.name == tool_name]
    if not matches:
        raise ValueError(f"Expected a {tool_name!r} tool call; received none")
    arguments = matches[0].arguments
    if any(call.arguments != arguments for call in matches[1:]):
        raise ValueError(
            f"Model returned conflicting {tool_name!r} tool calls; refusing ambiguous output"
        )
    return model.model_validate(_decode_stringified_containers(arguments, model))


def _decode_stringified_containers(
    arguments: dict[str, Any], model: type[BaseModel]
) -> dict[str, Any]:
    """Undo a common small-model habit of sending lists or objects as JSON strings.

    Only fields whose schema is a list or object are decoded, and only when the string
    parses to that container; everything is still validated by the Pydantic model.
    """
    properties = model.model_json_schema().get("properties", {})
    decoded = dict(arguments)
    for name, value in arguments.items():
        if not isinstance(value, str) or not _expects_container(properties.get(name, {})):
            continue
        parsed = _parse_container(value)
        if parsed is not None:
            decoded[name] = parsed
    return decoded


def _expects_container(schema: dict[str, Any]) -> bool:
    options = [schema, *schema.get("anyOf", [])]
    return any(option.get("type") in {"array", "object"} for option in options)


def _parse_container(text: str) -> list[Any] | dict[str, Any] | None:
    for parse in (json.loads, ast.literal_eval):
        try:
            parsed = parse(text.strip())
        except (ValueError, SyntaxError, TypeError):
            continue
        if isinstance(parsed, list | dict):
            return parsed
    return None


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
