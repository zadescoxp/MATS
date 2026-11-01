import pytest
from pydantic import BaseModel

from framework.agents.common import parse_tool_response
from framework.config.settings import ModelRoute, Settings
from framework.llm.client import LLMClient, LLMResponse, ToolCall, ToolDefinition


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
    assert captured["tool_choice"] == "required"
    assert captured["parallel_tool_calls"] is False
    assert captured["drop_params"] is True
    assert "num_ctx" not in captured


@pytest.mark.parametrize("provider", ["ollama", "ollama_chat"])
def test_ollama_uses_native_chat_with_a_larger_context_window(monkeypatch, provider) -> None:
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

    monkeypatch.setattr(
        llm_module, "completion", lambda **kwargs: captured.update(kwargs) or FakeResponse()
    )
    route = ModelRoute(provider=provider, model="llama3.2:3b", base_url="http://127.0.0.1:11434")
    LLMClient(Settings(ollama_num_ctx=8192, ollama_max_output_tokens=512)).invoke_with_tools(
        [{"role": "user", "content": "Call the tool."}],
        [ToolDefinition(name="echo", description="Echo.", parameters={"type": "object"})],
        route,
    )
    assert captured["model"] == "ollama_chat/llama3.2:3b"
    assert captured["num_ctx"] == 8192
    assert captured["max_tokens"] == 512
    assert captured["api_base"] == "http://127.0.0.1:11434"


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


def test_identical_duplicate_tool_calls_are_normalized() -> None:
    class Echo(BaseModel):
        ok: bool

    response = LLMResponse(
        tool_calls=[
            ToolCall(id="first", name="echo", arguments={"ok": True}),
            ToolCall(id="second", name="echo", arguments={"ok": True}),
        ]
    )
    assert parse_tool_response(response, "echo", Echo) == Echo(ok=True)


def test_conflicting_duplicate_tool_calls_are_rejected() -> None:
    class Echo(BaseModel):
        ok: bool

    response = LLMResponse(
        tool_calls=[
            ToolCall(id="first", name="echo", arguments={"ok": True}),
            ToolCall(id="second", name="echo", arguments={"ok": False}),
        ]
    )
    with pytest.raises(ValueError, match="conflicting"):
        parse_tool_response(response, "echo", Echo)


class _Findings(BaseModel):
    bias: str
    key_levels: list[float] = []
    notes: dict[str, str] = {}


def _response(arguments: dict) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id="1", name="submit", arguments=arguments)])


def test_tool_parser_decodes_lists_and_objects_sent_as_strings() -> None:
    parsed = parse_tool_response(
        _response({"bias": "[]", "key_levels": "[1.5, 2]", "notes": "{'a': 'b'}"}),
        "submit",
        _Findings,
    )
    assert parsed.bias == "[]"
    assert parsed.key_levels == [1.5, 2.0]
    assert parsed.notes == {"a": "b"}


def test_tool_parser_still_rejects_strings_that_are_not_containers() -> None:
    with pytest.raises(ValueError):
        parse_tool_response(
            _response({"bias": "neutral", "key_levels": "around 2400"}), "submit", _Findings
        )


def test_text_tool_calls_recovers_json_written_in_the_reply() -> None:
    from framework.llm.client import text_tool_calls

    content = '{\n "name": "submit_findings",\n "arguments": {"key_levels": [2250]}\n}'
    calls = text_tool_calls(content, {"submit_findings"})
    assert [(call.name, call.arguments) for call in calls] == [
        ("submit_findings", {"key_levels": [2250]})
    ]
    fenced = '```json\n{"name": "submit_findings", "parameters": "{\\"a\\": 1}"}\n```'
    assert text_tool_calls(fenced, {"submit_findings"})[0].arguments == {"a": 1}


def test_text_tool_calls_ignores_unoffered_tools_and_prose() -> None:
    from framework.llm.client import text_tool_calls

    assert text_tool_calls('{"name": "delete_everything", "arguments": {}}', {"submit"}) == []
    assert text_tool_calls("The market looks bullish {maybe}.", {"submit"}) == []


class _ScriptedLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.conversations: list[list[dict]] = []

    def invoke_with_tools(self, messages, tools, route):
        self.conversations.append(list(messages))
        return self.responses.pop(0)


def test_invoke_structured_sends_the_error_back_once_and_sums_usage() -> None:
    from framework.agents.common import invoke_structured

    llm = _ScriptedLLM(
        [
            LLMResponse(content="I think it is bullish.", prompt_tokens=10, cost=0.01),
            LLMResponse(
                tool_calls=[ToolCall(id="1", name="submit", arguments={"bias": "up"})],
                prompt_tokens=12,
                completion_tokens=3,
                cost=0.02,
            ),
        ]
    )
    tool = ToolDefinition(name="submit", description="Submit.", parameters={"type": "object"})

    parsed, response = invoke_structured(
        llm, [{"role": "user", "content": "go"}], tool, _Findings, "x"
    )

    assert parsed.bias == "up"
    assert response.tokens_used == 25
    assert response.cost == pytest.approx(0.03)
    assert "submit call was invalid" in llm.conversations[1][-1]["content"]


def test_invoke_structured_raises_after_the_repair_attempt() -> None:
    from framework.agents.common import invoke_structured

    llm = _ScriptedLLM([LLMResponse(content="no"), LLMResponse(content="still no")])
    tool = ToolDefinition(name="submit", description="Submit.", parameters={"type": "object"})

    with pytest.raises(ValueError, match="received none"):
        invoke_structured(llm, [{"role": "user", "content": "go"}], tool, _Findings, "x")
