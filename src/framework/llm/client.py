import json
from typing import Any

from litellm import completion
from pydantic import BaseModel, Field

from framework.config.settings import ModelRoute, Settings


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    @property
    def tokens_used(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def invoke_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        model_config: ModelRoute | str,
    ) -> LLMResponse:
        route = (
            self.settings.model_for(model_config) if isinstance(model_config, str) else model_config
        )
        if not route.supports_tools:
            raise ValueError(f"Model {route.model!r} is configured without tool-calling support")
        if not tools:
            raise ValueError("At least one tool schema is required for tool-calling requests")

        model_name = route.model
        if route.provider and "/" not in model_name and route.provider != "openai":
            model_name = f"{route.provider}/{model_name}"
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "tools": [tool.openai_schema() for tool in tools],
            "tool_choice": "auto",
        }
        if route.api_key:
            kwargs["api_key"] = route.api_key.get_secret_value()
        if route.base_url:
            kwargs["api_base"] = route.base_url

        response = completion(**kwargs)
        choice = response.choices[0].message
        calls: list[ToolCall] = []
        for call in choice.tool_calls or []:
            raw_arguments = call.function.arguments
            arguments = (
                json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            )
            if not isinstance(arguments, dict):
                raise ValueError(f"Tool {call.function.name!r} arguments must be a JSON object")
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        usage = response.usage
        hidden = getattr(response, "_hidden_params", {}) or {}
        return LLMResponse(
            content=choice.content,
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cost=float(hidden.get("response_cost", 0.0) or 0.0),
        )
