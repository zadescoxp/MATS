from pathlib import Path

from framework.config.connections import ConsoleConnections, snapshot_environment
from framework.config.settings import Settings


def test_saved_model_is_applied_and_secrets_stay_off_the_public_view(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "state.db",
        llm_provider="baseten",
        llm_model="env-model",
        llm_api_key="env-secret-key",
        bybit_api_key="env-bybit-key",
        bybit_api_secret="env-bybit-secret",
    )
    env = snapshot_environment(settings)
    connections = ConsoleConnections(tmp_path / "connections.json")
    model_id = connections.save_model(
        settings,
        {
            "name": "Desk",
            "provider": "openai",
            "model": "gpt-4.1",
            "api_key": "sk-test-key-1234",
        },
    )
    connections.select_model(settings, env, model_id)

    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-4.1"
    assert settings.model_for("strategy").model == "gpt-4.1"
    assert settings.model_for("strategy").api_key is not None
    assert settings.model_for("strategy").api_key.get_secret_value() == "sk-test-key-1234"

    public = connections.public(env)
    encoded = str(public)
    assert "sk-test-key-1234" not in encoded
    assert "env-secret-key" not in encoded
    assert "env-bybit-secret" not in encoded
    assert public["models"][1]["api_key_hint"] == "••••1234"
    assert public["active_model_id"] == model_id

    connections.select_model(settings, env, "environment")
    assert settings.llm_model == "env-model"
    assert settings.model_for("analyst").model == "env-model"


def test_bybit_keys_replace_live_settings_without_clearing_environment(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "state.db",
        bybit_api_key="env-bybit-key",
        bybit_api_secret="env-bybit-secret",
    )
    env = snapshot_environment(settings)
    connections = ConsoleConnections(tmp_path / "connections.json")
    connections.save_bybit(settings, env, "new-key-9876", "new-secret-4321", clear=False)
    assert settings.bybit_api_key is not None
    assert settings.bybit_api_key.get_secret_value() == "new-key-9876"
    public = connections.public(env)
    assert public["bybit_source"] == "saved"
    assert public["bybit_api_key_hint"] == "••••9876"
    assert "new-secret-4321" not in str(public)

    connections.save_bybit(settings, env, None, None, clear=True)
    assert settings.bybit_api_key is not None
    assert settings.bybit_api_key.get_secret_value() == "env-bybit-key"
