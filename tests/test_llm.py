import pytest

from framework.config.settings import ModelRoute, Settings
from framework.llm.client import LLMClient, ToolDefinition


def test_default_model_is_baseten_muse_for_every_role() -> None:
    settings = Settings(model_routes={})
    for role in ("analyst", "strategy", "critic", "coder", "dummy"):
        route = settings.model_for(role)
        assert route.provider == "baseten"
        assert route.model == "meta-models/muse-spark-open-1026"
        assert route.base_url == "https://inference.baseten.co/v1"


def test_llm_client_uses_openai_compatible_baseten_transport(monkeypatch) -> None:
    import framework.llm.client as llm_module

    captured = {}

    class FakeResponse:
        choices = [
            type(
                "Choice",
                (),
                {"message": type("Message", (), {"content": None, "tool_calls": []})()},
            )()
        ]
        usage = None
        _hidden_params = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(llm_module, "completion", fake_completion)
    client = LLMClient(
        Settings(
            llm_provider="baseten",
            llm_model="meta-models/muse-spark-open-1026",
            llm_base_url="https://inference.baseten.co/v1",
            model_routes={},
        )
    )
    client.invoke_with_tools(
        [{"role": "user", "content": "Call the tool."}],
        [
            ToolDefinition(
                name="echo",
                description="Echo.",
                parameters={"type": "object", "properties": {}},
            )
        ],
        "analyst",
    )
    assert captured["model"] == "openai/meta-models/muse-spark-open-1026"
    assert captured["api_base"] == "https://inference.baseten.co/v1"


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
