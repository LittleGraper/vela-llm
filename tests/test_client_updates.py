import json

import httpx
import pytest
from test_client_config import clients as clients
from test_model_context import catalog, model
from test_model_context import configured as configured

from client_config import CLIENTS, parse, read_bytes
from model_menu import ModelManager


def capable(name="model-a"):
    return {
        **model(name),
        "supported_endpoints": ["/responses", "/chat/completions"],
        "capabilities": {
            "supports": {
                "vision": True,
                "reasoning_effort": ["low", "medium", "high", "xhigh", "max"],
                "parallel_tool_calls": True,
            }
        },
    }


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("selection", [None, ["model-a"]])
def test_explicit_configure_fetches_live_capabilities(clients, monkeypatch, client, selection):
    plan = clients.plan(client, selected_models=selection)
    calls = []

    def refresh(path):
        calls.append(path)
        catalog(path.parent, [capable(), capable("new-model")])

    monkeypatch.setattr("client_config.refresh_model_cache", refresh)
    fresh = clients.refresh_plan(plan)
    assert calls == [clients.settings.model_cache_path]
    assert fresh.catalog_count == (2 if selection is None else 1)
    assert fresh.selected_models == selection
    assert not any(path.exists() for path in clients.paths(client))
    clients.apply(fresh)
    assert clients.plan(client).status == "Configured"


@pytest.mark.parametrize("client", CLIENTS)
def test_failed_live_refresh_keeps_client_and_state_bytes(clients, monkeypatch, client):
    clients.apply(clients.plan(client))
    plan = clients.plan(client)
    paths = [*clients.paths(client), plan.state_path, clients.settings.model_cache_path]
    before = {path: read_bytes(path) for path in paths}

    def fail(path):
        raise httpx.ConnectError("private-url-or-credential")

    monkeypatch.setattr("client_config.refresh_model_cache", fail)
    with pytest.raises(ValueError, match="refresh failed") as error:
        clients.refresh_plan(plan)
    assert "private-url-or-credential" not in str(error.value)
    assert before == {path: read_bytes(path) for path in paths}


def test_refresh_preserves_external_edit_in_flight(clients, monkeypatch):
    clients.apply(clients.plan("codex"))
    plan = clients.plan("codex")
    path = clients.paths("codex")[0]

    def edit(path_to_cache):
        path.write_text('model = "user-edit"\n', encoding="utf-8")

    monkeypatch.setattr("client_config.refresh_model_cache", edit)
    with pytest.raises(ValueError, match="changed during refresh"):
        clients.refresh_plan(plan)
    assert 'model = "user-edit"' in path.read_text()


def test_disappeared_selection_does_not_write_empty_catalog(clients, monkeypatch):
    clients.apply(clients.plan("codex", selected_models=["model-a"]))
    plan = clients.plan("codex")
    before = {c.path: c.before for c in plan.changes}
    monkeypatch.setattr(
        "client_config.refresh_model_cache", lambda path: catalog(path.parent, [model("other")])
    )
    with pytest.raises(ValueError, match="at least one"):
        clients.refresh_plan(plan)
    assert before == {p: read_bytes(p) for p in before}


@pytest.mark.parametrize("client", CLIENTS)
def test_context_save_and_delete_sync_immediately(clients, configured, monkeypatch, client):
    settings, _ = configured
    clients.apply(clients.plan(client))
    monkeypatch.setattr("client_config.ClientManager", lambda settings: clients)
    manager = ModelManager(settings)
    result = manager.save_context("model-a", "maximum")
    assert f"{CLIENTS[client]}: models synced" in result
    assert any("Restart Codex" in message for message in result)
    assert clients.plan(client).status == "Configured"
    files = [read_bytes(path) or b"" for path in clients.paths(client)]
    assert b"1050000" in b"".join(files)
    result = manager.delete_context("model-a")
    assert f"{CLIENTS[client]}: models synced" in result
    assert clients.plan(client).status == "Configured"
    assert b"272000" in b"".join(read_bytes(path) or b"" for path in clients.paths(client))


def test_context_sync_reports_conflict_without_reverting_preference(
    clients, configured, monkeypatch
):
    settings, _ = configured
    clients.apply(clients.plan("codex"))
    path = clients.paths("codex")[1]
    document = json.loads(path.read_bytes())
    document["models"][0]["description"] = "User edit"
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr("client_config.ClientManager", lambda settings: clients)
    result = ModelManager(settings).save_context("model-a", "maximum")
    assert result == ["Codex: Needs review (open /clients)"]
    assert settings.context_preferences()["model-a"]["mode"] == "maximum"
    assert path.read_bytes() == before


@pytest.mark.parametrize("client", CLIENTS)
def test_capability_projection_uses_each_client_schema(clients, configured, client):
    _, config = configured
    catalog(config.parent, [capable(), model("text-only")])
    clients.apply(clients.plan(client))
    path = clients.paths(client)[1 if client == "codex" else 0]
    doc = parse(path, path.read_bytes())
    if client == "codex":
        first, second = doc["models"]
        assert first["input_modalities"] == ["text", "image"]
        assert first["supports_parallel_tool_calls"] is True
        assert first["default_reasoning_level"] == "medium"
        assert second["input_modalities"] == ["text"]
    elif client == "kimi":
        assert doc["models"]["vela/model-a"]["capabilities"] == [
            "image_in",
            "thinking",
            "always_thinking",
        ]
        assert doc["models"]["vela/text-only"]["capabilities"] == []
    else:
        root = doc["llm-pi-ai"]
        entries = [entry for provider in root["providers"].values() for entry in provider["models"]]
        first = next(entry for entry in entries if entry["id"] == "model-a")
        second = next(entry for entry in entries if entry["id"] == "text-only")
        assert first["input"] == ["text", "image"]
        assert first["maxTokens"] == 128000
        assert second["input"] == ["text"]
        assert first["reasoningEfforts"] == {
            effort: effort for effort in ["low", "medium", "high", "xhigh", "max"]
        }


@pytest.mark.parametrize("record", ["{}", "{malformed legacy record"])
def test_retired_pi_is_not_configurable_or_synchronized(clients, configured, monkeypatch, record):
    settings, config = configured
    pi_home = clients.home / ".pi" / "agent"
    pi_home.mkdir(parents=True)
    files = {
        pi_home / "models.json": '{"providers":{"vela":{"apiKey":"old-key"}}}',
        pi_home / "settings.json": '{"defaultProvider":"github-copilot"}',
        pi_home / "auth.json": '{"github-copilot":{"type":"oauth","access":"private"}}',
        clients.state_dir / "pi.json": record,
    }
    for path, contents in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    before = {path: path.read_bytes() for path in files}
    assert tuple(CLIENTS) == ("dsh", "codex", "kimi")
    with pytest.raises(ValueError, match="Unsupported client"):
        clients.plan("pi")
    with pytest.raises(ValueError, match="Unsupported client"):
        clients.paths("pi")
    clients.apply(clients.plan("codex"))
    catalog(config.parent, [model(), model("new-model")])
    assert clients.sync_configured() == ["Codex: models synced"]
    monkeypatch.setattr("client_config.ClientManager", lambda settings: clients)
    assert "Codex: models synced" in ModelManager(settings).save_context("model-a", "maximum")
    assert before == {path: path.read_bytes() for path in files}
