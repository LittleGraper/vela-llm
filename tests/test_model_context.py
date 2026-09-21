from __future__ import annotations

import json
import subprocess
import sys
import tomllib

import pytest
from fastapi.testclient import TestClient

import github_copilot_models
from main import app
from model_context import resolve_context
from model_menu import ModelManager, print_model_snapshot
from model_store import write_context, write_default_model
from settings import Settings, get_settings


def model(name="model-a", default=272000, maximum=1050000):
    return {
        "name": name,
        "upstream": f"github_copilot/{name}",
        "max_tokens": 1178000,
        "max_input_tokens": maximum,
        "max_output_tokens": 128000,
        "default_context_size": default,
        "context_size_options": sorted({default, maximum}),
    }


def catalog(path, entries):
    (path / "models-cache.json").write_text(
        json.dumps({"models": entries, "fetched_at": "2026-09-20T09:30:00+00:00"}),
        encoding="utf-8",
    )


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / "models.toml"
    path.write_text('# Keep this comment\n[models]\ndefault = "model-a"\n', encoding="utf-8")
    catalog(tmp_path, [model(), model("model-b")])
    monkeypatch.setenv("VELA_LLM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("VELA_LLM_MODELS_CONFIG", str(path))
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    get_settings.cache_clear()
    settings = Settings(LOCAL_API_KEY="test-key", VELA_LLM_MODELS_CONFIG=path)
    yield settings, path
    get_settings.cache_clear()


def test_persistence_preserves_other_models_default_comments_and_unknown_keys(configured):
    _, path = configured
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n[extra]\nkeep = [1, 2]\n")
    write_context(path, 'model.a/"quoted"', "custom", 272000)
    write_context(path, "model-b", "maximum")
    write_default_model(path, "model-b")
    contents = path.read_text(encoding="utf-8")
    data = tomllib.loads(contents)
    assert "# Keep this comment" in contents
    assert data["extra"]["keep"] == [1, 2]
    assert data["models"]["default"] == "model-b"
    assert data["models"]["context"]['model.a/"quoted"']["size"] == 272000
    write_context(path, 'model.a/"quoted"', "maximum")
    assert "size" not in tomllib.loads(path.read_text())["models"]["context"]['model.a/"quoted"']
    write_context(path, 'model.a/"quoted"', "auto")
    assert set(tomllib.loads(path.read_text())["models"]["context"]) == {"model-b"}


def test_concurrent_cli_writers_keep_both_updates(configured):
    _, path = configured
    code = (
        "from model_store import write_context; import sys; "
        "write_context(sys_path, sys.argv[2], 'maximum')"
    )
    code = "from pathlib import Path; import sys; sys_path=Path(sys.argv[1]); " + code
    processes = [
        subprocess.Popen([sys.executable, "-c", code, str(path), name])
        for name in ("model-a", "model-b")
    ]
    for process in processes:
        assert process.wait(timeout=15) == 0
    assert set(tomllib.loads(path.read_text())["models"]["context"]) == {"model-a", "model-b"}


def test_atomic_save_failure_keeps_previous_configuration(configured, monkeypatch):
    _, path = configured
    before = path.read_bytes()

    def fail(*args):
        raise OSError("disk error")

    monkeypatch.setattr(type(path), "replace", fail)
    with pytest.raises(OSError):
        write_context(path, "model-a", "maximum")
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "preference,expected",
    [
        (None, 272000),
        ({"mode": "maximum"}, 1050000),
        ({"mode": "custom", "size": 272000}, 272000),
    ],
)
def test_resolves_modes(preference, expected):
    assert resolve_context(model(), preference)["context_size"] == expected


@pytest.mark.parametrize(
    "preference",
    [
        {"mode": "custom", "size": 999},
        {"mode": "custom", "size": True},
        {"mode": "custom", "size": "272000"},
        {"mode": []},
        "broken",
    ],
)
def test_invalid_preferences_fall_back_visibly(preference):
    state = resolve_context(model(), preference)
    assert state["context_size"] == 272000
    assert state["context_status"] == "needs_review"


def test_unknown_default_is_not_invented():
    assert resolve_context({"max_input_tokens": 200000})["context_size"] is None
    assert resolve_context({}, {"mode": "maximum"})["context_status"] == "unknown"


def test_save_revalidates_against_latest_catalog(configured):
    settings, path = configured
    menu = ModelManager(settings)
    catalog(path.parent, [model(maximum=400000)])
    with pytest.raises(ValueError, match="no longer available"):
        menu.save_context("model-a", "custom", 1050000)
    assert not settings.context_preferences()


def test_unavailable_preference_retained_recovered_and_deleted_explicitly(configured):
    settings, path = configured
    write_context(path, "model-a", "custom", 272000)
    catalog(path.parent, [model("model-b")])
    menu = ModelManager(settings)
    assert menu.unavailable() == ["model-a"]
    assert settings.context_preferences()["model-a"]["size"] == 272000
    catalog(path.parent, [model()])
    menu.reload()
    assert "model-a" not in menu.unavailable()
    assert settings.model_registry()[0]["context_size"] == 272000
    catalog(path.parent, [model("model-b")])
    menu.reload()
    menu.delete_context("model-a")
    assert "model-a" not in settings.context_preferences()
    assert settings.default_model == "model-a"


@pytest.mark.parametrize("bad", [[], [{"name": "missing-upstream"}], [model(), model()]])
def test_bad_refresh_preserves_cache_and_preferences(configured, monkeypatch, bad):
    settings, path = configured
    write_context(path, "model-a", "maximum")
    before = settings.model_cache_path.read_bytes()
    monkeypatch.setattr(github_copilot_models, "fetch_available_models", lambda: bad)
    with pytest.raises(ValueError):
        github_copilot_models.refresh_model_cache(settings.model_cache_path)
    assert settings.model_cache_path.read_bytes() == before
    assert settings.context_preferences()["model-a"]["mode"] == "maximum"


def test_model_endpoint_observes_saves_and_catalog_changes_without_restart(configured):
    _, path = configured
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-key"}

    def entry():
        return client.get("/v1/models", headers=headers).json()["data"][0]

    assert entry()["context_size"] == 272000
    write_context(path, "model-a", "maximum")
    assert entry()["context_size"] == 1050000
    catalog(path.parent, [model(default=200000, maximum=400000)])
    assert entry()["context_size"] == 400000
    write_context(path, "model-a", "custom", 272000)
    assert entry()["context_status"] == "needs_review"
    assert entry()["configured_context_size"] == 272000
    assert entry()["context_size"] == 200000
    write_context(path, "model-a", "auto")
    assert entry()["context_mode"] == "auto"


@pytest.mark.parametrize(
    "endpoint",
    [
        "/v1/chat/completions",
        "/v1/responses",
        "/v1/messages",
        "/v1/embeddings",
    ],
)
def test_missing_default_returns_error_without_inference_or_discovery(
    configured, monkeypatch, endpoint
):
    _, path = configured
    catalog(path.parent, [model("model-b")])

    def unexpected(*args, **kwargs):
        raise AssertionError("No discovery or inference expected")

    monkeypatch.setattr(github_copilot_models, "fetch_available_models", unexpected)
    monkeypatch.setattr("main._litellm", unexpected)
    result = TestClient(app).post(endpoint, headers={"Authorization": "Bearer test-key"}, json={})
    assert result.status_code == 404
    assert "unavailable" in result.json()["error"]["message"]


def test_offline_noninteractive_menu_does_not_refresh_or_edit(configured, monkeypatch, capsys):
    settings, path = configured
    before = path.read_bytes()

    def unexpected(*args):
        raise AssertionError("No refresh or key input in noninteractive mode")

    monkeypatch.setattr(github_copilot_models, "fetch_available_models", unexpected)
    print_model_snapshot(settings)
    output = capsys.readouterr().out
    assert "model-a" in output and "\033" not in output
    assert path.read_bytes() == before


def test_inference_registration_observes_new_context_without_restart(configured, monkeypatch):
    _, path = configured
    registrations = []
    requests = []

    class FakeLiteLLM:
        @staticmethod
        def register_model(metadata):
            registrations.append(metadata)

        @staticmethod
        async def acompletion(**kwargs):
            requests.append(kwargs)
            return {"choices": []}

    monkeypatch.setitem(sys.modules, "litellm", FakeLiteLLM)
    monkeypatch.setattr("github_copilot_patch.apply_github_copilot_oauth_patch", lambda: None)
    client = TestClient(app)
    payload = {"messages": [], "max_tokens": 16}
    for mode, expected in [("auto", 272000), ("maximum", 1050000), ("custom", 272000)]:
        write_context(path, "model-a", mode, 272000 if mode == "custom" else None)
        result = client.post(
            "/v1/chat/completions", headers={"Authorization": "Bearer test-key"}, json=payload
        )
        assert result.status_code == 200
        metadata = registrations[-1]["github_copilot/model-a"]
        assert metadata["max_input_tokens"] == expected
        assert metadata["max_tokens"] == 1178000
        assert metadata["max_output_tokens"] == 128000
        assert requests[-1]["max_tokens"] == 16


def test_model_registry_without_cache_does_not_fetch(configured, monkeypatch):
    settings, _ = configured
    settings.model_cache_path.unlink()

    def unexpected(*args):
        raise AssertionError("Catalog lookup must not trigger discovery")

    monkeypatch.setattr(github_copilot_models, "fetch_available_models", unexpected)
    assert settings.model_registry()[0]["context_size"] is None
