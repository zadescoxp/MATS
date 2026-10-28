import pytest

from framework.config.settings import ModelRoute, Settings
from framework.llm.client import LLMClient, ToolDefinition


def test_shared_baseten_route_is_used_for_each_agent_role() -> None:
    settings = Settings(
        llm_provider="baseten",
        llm_model="meta-models/muse-spark-open-1026",
        llm_base_url="https://inference.baseten.co/v1",
        model_routes={},
    )
    for role in ("analyst", "strategy", "critic", "coder", "dummy"):
        route = settings.model_for(role)
        assert route.provider == "baseten"
        assert route.model == "meta-models/muse-spark-open-1026"
        assert route.base_url == "https://inference.baseten.co/v1"


def test_role_route_overrides_shared_model() -> None:
    settings = Settings(
        llm_provider="baseten",
        llm_model="meta-models/muse-spark-open-1026",
        model_routes={"critic": ModelRoute(provider="openai", model="critic-model")},
    )
    assert settings.model_for("critic").model == "critic-model"
    assert settings.model_for("analyst").model == "meta-models/muse-spark-open-1026"


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
