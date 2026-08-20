from __future__ import annotations

from config_files import active_config_dir, default_models_toml, ensure_config_files
from settings import Settings


def test_models_toml_drives_default_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VELA_LLM_DISABLE_DYNAMIC_MODELS", "1")
    monkeypatch.delenv("VELA_LLM_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("VELA_LLM_MODEL_ALIASES", raising=False)

    config = tmp_path / "models.toml"
    config.write_text(
        """
[models]
default = "gpt-4o"
""".strip(),
        encoding="utf-8",
    )

    settings = Settings(
        LOCAL_API_KEY="sk-test",
        VELA_LLM_MODELS_CONFIG=config,
        VELA_LLM_DEFAULT_MODEL="",
        VELA_LLM_MODEL_ALIASES="",
    )

    assert settings.aliases == ["gpt-4o"]
    assert settings.default_model == "gpt-4o"
    assert settings.upstream_model(None) == "github_copilot/gpt-4o"


def test_stale_env_model_overrides_are_ignored(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VELA_LLM_DISABLE_DYNAMIC_MODELS", "1")

    config = tmp_path / "models.toml"
    config.write_text(
        """
[models]
default = "gpt-4o"
""".strip(),
        encoding="utf-8",
    )

    settings = Settings(
        LOCAL_API_KEY="sk-test",
        VELA_LLM_MODELS_CONFIG=config,
        VELA_LLM_DEFAULT_MODEL="gpt-5.5",
        VELA_LLM_MODEL_ALIASES="gpt-5.5,gpt-4.1",
    )

    assert settings.aliases == ["gpt-4o"]
    assert settings.default_model == "gpt-4o"
    assert settings.upstream_model("gpt-4o") == "github_copilot/gpt-4o"


def test_models_toml_example_is_used_as_default_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VELA_LLM_DISABLE_DYNAMIC_MODELS", "1")
    monkeypatch.setenv("VELA_LLM_CONFIG_DIR", str(tmp_path))
    example = tmp_path / "models.toml.example"
    example.write_text(
        """
[models]
default = "example-model"
""".strip(),
        encoding="utf-8",
    )

    settings = Settings(
        LOCAL_API_KEY="sk-test",
        VELA_LLM_MODELS_CONFIG="models.toml",
        VELA_LLM_DEFAULT_MODEL="",
        VELA_LLM_MODEL_ALIASES="",
    )

    assert settings.aliases == ["example-model"]
    assert settings.default_model == "example-model"
    assert settings.upstream_model("example-model") == "github_copilot/example-model"


def test_default_model_is_added_to_registry_when_alias_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VELA_LLM_DISABLE_DYNAMIC_MODELS", "1")

    config = tmp_path / "models.toml"
    config.write_text(
        """
[models]
default = "gpt-4o"
""".strip(),
        encoding="utf-8",
    )

    settings = Settings(
        LOCAL_API_KEY="sk-test",
        VELA_LLM_MODELS_CONFIG=config,
    )

    assert settings.aliases == ["gpt-4o"]
    assert settings.upstream_model(None) == "github_copilot/gpt-4o"


def test_ensure_config_files_uses_packaged_models_template(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VELA_LLM_CONFIG_DIR", str(tmp_path))

    ensure_config_files()

    assert (tmp_path / "models.toml").read_text(encoding="utf-8") == default_models_toml()
    assert 'default = "gpt-4o"' in default_models_toml()


def test_active_config_dir_defaults_to_user_config_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("VELA_LLM_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOCAL_API_KEY=local\n", encoding="utf-8")
    (tmp_path / "models.toml").write_text('[models]\ndefault = "local"\n', encoding="utf-8")

    assert active_config_dir() == tmp_path / "AppData" / "vela-llm"
