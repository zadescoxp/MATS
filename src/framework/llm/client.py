import json
import logging
from time import perf_counter
from typing import Any

from litellm import completion
from pydantic import BaseModel, Field

from framework.config.settings import ModelRoute, Settings

logger = logging.getLogger(__name__)


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
        if route.provider == "baseten":
            # Use Baseten's OpenAI-compatible Model API, not LiteLLM's
            # deployment-specific Baseten adapter, which routes slash IDs to /predict.
            model_name = f"openai/{route.model}"
        elif route.provider in {"ollama", "ollama_chat"}:
            # /api/chat supports native tool calls; LiteLLM's ollama/ route emulates them
            # through /api/generate, which can run until the context window is full.
            model_name = f"ollama_chat/{model_name.split('/', 1)[-1]}"
        elif route.provider != "openai" and not model_name.startswith(f"{route.provider}/"):
            model_name = f"{route.provider}/{model_name}"
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "tools": [tool.openai_schema() for tool in tools],
            "tool_choice": "required",
            "parallel_tool_calls": False,
            # Local providers such as Ollama reject parallel_tool_calls; drop what they lack.
            "drop_params": True,
        }
        if model_name.startswith("ollama_chat/"):
            kwargs["num_ctx"] = self.settings.ollama_num_ctx
            kwargs["max_tokens"] = self.settings.ollama_max_output_tokens
        if route.api_key:
            kwargs["api_key"] = route.api_key.get_secret_value()
        if route.base_url:
            kwargs["api_base"] = route.base_url

        started_at = perf_counter()
        logger.info(
            "LLM request started (provider=%s, model=%s, messages=%d, tools=%s).",
            route.provider,
            route.model,
            len(messages),
            ",".join(tool.name for tool in tools),
        )
        try:
            response = completion(**kwargs)
        except Exception:
            logger.exception(
                "LLM request failed (provider=%s, model=%s, duration=%.2fs).",
                route.provider,
                route.model,
                perf_counter() - started_at,
            )
            raise
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
        result = LLMResponse(
            content=choice.content,
            tool_calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cost=float(hidden.get("response_cost", 0.0) or 0.0),
        )
        logger.info(
            "LLM response received in %.2fs (tokens=%d, cost=$%.4f, tool_calls=%s).",
            perf_counter() - started_at,
            result.tokens_used,
            result.cost,
            ",".join(call.name for call in result.tool_calls) or "none",
        )
        return result
