from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

DEFAULT_ENV_TEMPLATE = """LOCAL_API_KEY={api_key}
VELA_LLM_HOST=127.0.0.1
VELA_LLM_PORT=4000
VELA_LLM_MODELS_CONFIG=models.toml
VELA_LLM_LITELLM_CONFIG=litellm.yaml
VELA_LLM_LOG_LEVEL=info

# Optional: choose where LiteLLM stores GitHub Copilot OAuth tokens.
# GITHUB_COPILOT_TOKEN_DIR=~/.config/litellm/github_copilot
"""

DEFAULT_MODELS_TOML = """[models]
default = "gpt-4"
"""


def active_config_dir() -> Path:
    configured = os.getenv("VELA_LLM_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser().resolve()

    return user_config_dir()


def user_config_dir() -> Path:
    if sys.platform == "win32":
        root = os.getenv("APPDATA")
        if root:
            return Path(root) / "vela-llm"
    root = os.getenv("XDG_CONFIG_HOME")
    if root:
        return Path(root) / "vela-llm"
    return Path.home() / ".config" / "vela-llm"


def ensure_config_files() -> Path:
    config_dir = active_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    env_file = config_dir / ".env"
    if not env_file.exists():
        api_key = f"sk-local-{secrets.token_urlsafe(24)}"
        env_file.write_text(DEFAULT_ENV_TEMPLATE.format(api_key=api_key), encoding="utf-8")

    models_file = config_dir / "models.toml"
    if not models_file.exists():
        models_file.write_text(default_models_toml(), encoding="utf-8")

    return config_dir


def env_file_for_settings() -> Path:
    return active_config_dir() / ".env"


def default_models_toml() -> str:
    installed_example = Path(__file__).resolve().with_name("models.toml.example")
    if installed_example.exists():
        return installed_example.read_text(encoding="utf-8")
    source_tree_example = Path(__file__).resolve().parents[1] / "models.toml.example"
    if source_tree_example.exists():
        return source_tree_example.read_text(encoding="utf-8")
    return DEFAULT_MODELS_TOML


def resolve_config_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return active_config_dir() / path
