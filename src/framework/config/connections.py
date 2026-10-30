import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from framework.config.settings import ModelRoute, Settings

_PROVIDERS = {"openai", "anthropic", "gemini", "ollama", "baseten", "custom"}
_ROLES = ("analyst", "strategy", "critic", "coder", "dummy")
_DEFAULT_BASE_URLS = {
    "ollama": "http://127.0.0.1:11434",
    "baseten": "https://inference.baseten.co/v1",
}


@dataclass
class EnvironmentCredentials:
    provider: str
    model: str
    api_key: SecretStr | None
    base_url: str | None
    supports_tools: bool
    routes: dict[str, ModelRoute]
    bybit_api_key: SecretStr | None
    bybit_api_secret: SecretStr | None


def snapshot_environment(settings: Settings) -> EnvironmentCredentials:
    return EnvironmentCredentials(
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        supports_tools=settings.llm_supports_tools,
        routes={role: route.model_copy(deep=True) for role, route in settings.model_routes.items()},
        bybit_api_key=settings.bybit_api_key,
        bybit_api_secret=settings.bybit_api_secret,
    )


def _secret_text(value: SecretStr | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def _hint(value: str | None) -> str | None:
    if not value:
        return None
    tail = value[-4:]
    return f"••••{tail}"


def _apply_model(settings: Settings, model: dict[str, Any]) -> None:
    provider = "openai" if model["provider"] == "custom" else model["provider"]
    api_key = model.get("api_key")
    route = ModelRoute(
        provider=provider,
        model=model["model"],
        api_key=SecretStr(api_key) if api_key else None,
        base_url=model.get("base_url"),
        supports_tools=bool(model.get("supports_tools", True)),
    )
    settings.llm_provider = route.provider
    settings.llm_model = route.model
    settings.llm_api_key = route.api_key
    settings.llm_base_url = route.base_url
    settings.llm_supports_tools = route.supports_tools
    settings.model_routes = {role: route.model_copy(deep=True) for role in _ROLES}


def _restore_models(settings: Settings, env: EnvironmentCredentials) -> None:
    settings.llm_provider = env.provider
    settings.llm_model = env.model
    settings.llm_api_key = env.api_key
    settings.llm_base_url = env.base_url
    settings.llm_supports_tools = env.supports_tools
    settings.model_routes = {
        role: route.model_copy(deep=True) for role, route in env.routes.items()
    }


class ConsoleConnections:
    def __init__(self, path: Path):
        self.path = path
        self.bybit_api_key: str | None = None
        self.bybit_api_secret: str | None = None
        self.models: list[dict[str, Any]] = []
        self.active_model_id: str | None = None

    def load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("Saved connections file is not an object.")
        key = raw.get("bybit_api_key")
        secret = raw.get("bybit_api_secret")
        self.bybit_api_key = key if isinstance(key, str) and key else None
        self.bybit_api_secret = secret if isinstance(secret, str) and secret else None
        models = raw.get("models")
        self.models = (
            [item for item in models if isinstance(item, dict)] if isinstance(models, list) else []
        )
        active = raw.get("active_model_id")
        self.active_model_id = active if isinstance(active, str) else None
        if self.active_model_id and not self._find(self.active_model_id):
            self.active_model_id = None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bybit_api_key": self.bybit_api_key,
            "bybit_api_secret": self.bybit_api_secret,
            "models": self.models,
            "active_model_id": self.active_model_id,
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def apply(self, settings: Settings) -> None:
        if self.bybit_api_key:
            settings.bybit_api_key = SecretStr(self.bybit_api_key)
        if self.bybit_api_secret:
            settings.bybit_api_secret = SecretStr(self.bybit_api_secret)
        active = self._find(self.active_model_id) if self.active_model_id else None
        if active:
            _apply_model(settings, active)

    def public(self, env: EnvironmentCredentials) -> dict[str, Any]:
        saved_key = bool(self.bybit_api_key and self.bybit_api_secret)
        env_key = bool(env.bybit_api_key and env.bybit_api_secret)
        key_hint = _hint(self.bybit_api_key) or _hint(_secret_text(env.bybit_api_key))
        secret_hint = _hint(self.bybit_api_secret) or _hint(_secret_text(env.bybit_api_secret))
        if saved_key:
            source = "saved"
        elif env_key or key_hint or secret_hint:
            source = "environment"
        else:
            source = "missing"
        return {
            "bybit_api_key_hint": key_hint,
            "bybit_api_secret_hint": secret_hint,
            "bybit_configured": saved_key or env_key,
            "bybit_source": source,
            "active_model_id": self.active_model_id or "environment",
            "models": [
                self._public_environment(env),
                *[self._public_model(item) for item in self.models],
            ],
        }

    def save_bybit(
        self,
        settings: Settings,
        env: EnvironmentCredentials,
        api_key: str | None,
        api_secret: str | None,
        *,
        clear: bool,
    ) -> None:
        if clear:
            self.bybit_api_key = None
            self.bybit_api_secret = None
            settings.bybit_api_key = env.bybit_api_key
            settings.bybit_api_secret = env.bybit_api_secret
            self.save()
            return
        if api_key is not None:
            api_key = api_key.strip()
        if api_secret is not None:
            api_secret = api_secret.strip()
        if not api_key and not api_secret:
            raise ValueError("Enter a Bybit API key or secret to save.")
        if api_key:
            self.bybit_api_key = api_key
            settings.bybit_api_key = SecretStr(api_key)
        if api_secret:
            self.bybit_api_secret = api_secret
            settings.bybit_api_secret = SecretStr(api_secret)
        self.save()

    def save_model(self, settings: Settings, raw: dict[str, Any]) -> str:
        model = self._normalize(raw)
        existing = self._find(model["id"]) if raw.get("id") else None
        if existing:
            if not model.get("api_key"):
                model["api_key"] = existing.get("api_key")
            self.models = [model if item["id"] == model["id"] else item for item in self.models]
        else:
            if model["provider"] != "ollama" and not model.get("api_key"):
                raise ValueError("An API key is required for this provider.")
            self.models.append(model)
        if self.active_model_id == model["id"]:
            _apply_model(settings, model)
        self.save()
        return str(model["id"])

    def delete_model(self, settings: Settings, env: EnvironmentCredentials, model_id: str) -> None:
        if model_id == "environment":
            raise ValueError("The environment model cannot be deleted.")
        if not self._find(model_id):
            raise ValueError("Saved model not found.")
        self.models = [item for item in self.models if item["id"] != model_id]
        if self.active_model_id == model_id:
            self.active_model_id = None
            _restore_models(settings, env)
        self.save()

    def select_model(self, settings: Settings, env: EnvironmentCredentials, model_id: str) -> None:
        if model_id == "environment":
            self.active_model_id = None
            _restore_models(settings, env)
            self.save()
            return
        model = self._find(model_id)
        if model is None:
            raise ValueError("Saved model not found.")
        self.active_model_id = model_id
        _apply_model(settings, model)
        self.save()

    def _find(self, model_id: str | None) -> dict[str, Any] | None:
        return next((item for item in self.models if item.get("id") == model_id), None)

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        provider = raw.get("provider")
        if provider not in _PROVIDERS:
            raise ValueError(
                "Choose OpenAI, Anthropic, Google, Ollama, Baseten, or a custom endpoint."
            )
        name = raw.get("name")
        model_name = raw.get("model")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Model name is required.")
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("Model id is required.")
        base_url = raw.get("base_url")
        if base_url is not None and not isinstance(base_url, str):
            raise ValueError("Base URL must be a string.")
        base_url = (base_url or "").strip() or _DEFAULT_BASE_URLS.get(str(provider))
        if provider == "custom" and not base_url:
            raise ValueError("A custom endpoint needs a base URL.")
        if base_url and not base_url.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://.")
        api_key = raw.get("api_key")
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("API key must be a string.")
        api_key = (api_key or "").strip() or None
        model_id = raw.get("id")
        if model_id is not None and (not isinstance(model_id, str) or model_id == "environment"):
            raise ValueError("Model id is invalid.")
        if not model_id:
            model_id = str(uuid.uuid4())
        supports_tools = raw.get("supports_tools", True)
        if not isinstance(supports_tools, bool):
            raise ValueError("supports_tools must be a boolean.")
        return {
            "id": model_id,
            "name": name.strip()[:80],
            "provider": provider,
            "model": model_name.strip()[:200],
            "base_url": base_url,
            "api_key": api_key,
            "supports_tools": supports_tools,
        }

    def _public_model(self, model: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": model["id"],
            "name": model["name"],
            "provider": model["provider"],
            "model": model["model"],
            "base_url": model.get("base_url"),
            "supports_tools": bool(model.get("supports_tools", True)),
            "api_key_hint": _hint(model.get("api_key")),
            "builtin": False,
        }

    def _public_environment(self, env: EnvironmentCredentials) -> dict[str, Any]:
        return {
            "id": "environment",
            "name": "Environment default",
            "provider": env.provider,
            "model": env.model,
            "base_url": env.base_url,
            "supports_tools": env.supports_tools,
            "api_key_hint": _hint(_secret_text(env.api_key)),
            "builtin": True,
        }
