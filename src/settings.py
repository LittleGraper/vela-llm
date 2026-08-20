import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from config_files import env_file_for_settings, resolve_config_path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    local_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LOCAL_API_KEY", "VELA_LLM_LOCAL_API_KEY"),
    )
    host: str = Field(default="127.0.0.1", validation_alias="VELA_LLM_HOST")
    port: int = Field(default=4000, validation_alias="VELA_LLM_PORT")
    models_config: Path = Field(
        default=Path("models.toml"),
        validation_alias="VELA_LLM_MODELS_CONFIG",
    )
    litellm_config: Path = Field(
        default=Path("litellm.yaml"),
        validation_alias="VELA_LLM_LITELLM_CONFIG",
    )
    log_level: str = Field(default="info", validation_alias="VELA_LLM_LOG_LEVEL")
    copilot_token_dir: str | None = Field(default=None, validation_alias="GITHUB_COPILOT_TOKEN_DIR")

    @property
    def aliases(self) -> list[str]:
        return [model["name"] for model in self.model_registry()]

    @property
    def default_model(self) -> str:
        data = self._models_data()
        default = data.get("models", {}).get("default")
        if isinstance(default, str) and default:
            return default
        aliases = self.aliases
        return aliases[0] if aliases else "gpt-4"

    def upstream_model(self, model: str | None) -> str:
        selected = model or self.default_model
        if "/" in selected:
            return selected
        for entry in self.model_registry():
            if entry["name"] == selected:
                return entry["upstream"]
        return f"github_copilot/{selected}"

    def model_registry(self) -> list[dict[str, str]]:
        dynamic = self.dynamic_model_registry()
        if dynamic:
            return dynamic
        return self.local_model_registry()

    def dynamic_model_registry(self) -> list[dict[str, str]]:
        if self._dynamic_models_disabled():
            return []
        try:
            from github_copilot_models import fetch_available_models

            registry = fetch_available_models()
        except Exception:
            return []
        return self._with_default_model(registry)

    def local_model_registry(self) -> list[dict[str, str]]:
        models_data = self._models_data().get("models", {})
        default = models_data.get("default")
        if isinstance(default, str) and default:
            return [{"name": default, "upstream": self._default_upstream(default)}]
        return [{"name": "gpt-4", "upstream": "github_copilot/gpt-4"}]

    def _with_default_model(self, registry: list[dict[str, str]]) -> list[dict[str, str]]:
        default = self._models_data().get("models", {}).get("default")
        if (
            isinstance(default, str)
            and default
            and default not in {entry["name"] for entry in registry}
        ):
            registry.insert(
                0,
                {"name": default, "upstream": self._default_upstream(default)},
            )
        return registry

    def _dynamic_models_disabled(self) -> bool:
        value = os.getenv("VELA_LLM_DISABLE_DYNAMIC_MODELS", "")
        return value.lower() in {"1", "true", "yes", "on"}

    def validate_runtime(self) -> None:
        if not self.local_api_key:
            msg = "LOCAL_API_KEY is required. Copy .env.example to .env and set a local key."
            raise RuntimeError(msg)
        if not self.aliases:
            msg = f"No models configured. Add aliases to {self.models_config}."
            raise RuntimeError(msg)

    def _models_data(self) -> dict[str, Any]:
        config_path = resolve_config_path(self.models_config)
        if not config_path.exists() and config_path.name == "models.toml":
            example_path = config_path.with_name("models.toml.example")
            if example_path.exists():
                config_path = example_path
        if not config_path.exists():
            return {"models": {"default": "gpt-4", "aliases": []}}
        with config_path.open("rb") as config_file:
            return tomllib.load(config_file)

    def _default_upstream(self, model: str) -> str:
        if "/" in model:
            return model
        return f"github_copilot/{model}"


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=env_file_for_settings())
