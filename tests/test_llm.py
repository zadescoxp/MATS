import pytest

from framework.config.settings import ModelRoute, Settings
from framework.llm.client import LLMClient, ToolDefinition


def test_llm_rejects_routes_without_tool_calling_support() -> None:
    settings = Settings(model_routes={"analyst": ModelRoute(supports_tools=False)})
    client = LLMClient(settings)
    with pytest.raises(ValueError, match="does not support tool calling"):
        client.invoke_with_tools(
            [{"role": "user", "content": "test"}],
            [
                ToolDefinition(
                    name="test",
                    description="test",
                    parameters={"type": "object", "properties": {}},
                )
            ],
            "analyst",
        )
