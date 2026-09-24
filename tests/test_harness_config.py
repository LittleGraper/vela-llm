import json

import pytest
from test_client_config import clients as clients
from test_client_config import configured_model_names
from test_model_context import catalog, model
from test_model_context import configured as configured

import client_config
from client_config import parse, read_bytes


@pytest.mark.parametrize("client", ["codex", "kimi"])
def test_custom_selection_sync_and_delete_stop_future_sync(clients, configured, client):
    _, config = configured
    clients.apply(clients.plan(client, selected_models=["model-b"]))
    assert clients.plan(client).selected_models == ["model-b"]
    assert configured_model_names(clients, client) == ["model-b"]
    catalog(config.parent, [model(), model("model-b"), model("new-model")])
    assert clients.sync_configured() == []
    assert configured_model_names(clients, client) == ["model-b"]
    clients.apply(clients.plan(client, selected_models=None))
    assert set(configured_model_names(clients, client)) == {"model-a", "model-b", "new-model"}
    removal = clients.plan(client, remove=True)
    clients.apply(removal)
    assert not removal.state_path.exists()
    assert not clients.plan(client, remove=True).has_configuration
    assert clients.plan(client).status == "Not configured"
    assert clients.sync_configured() == []


@pytest.mark.parametrize("client", ["codex", "kimi"])
def test_switching_models_in_client_does_not_block_sync(clients, configured, client):
    _, config = configured
    clients.apply(clients.plan(client))
    path = clients.paths(client)[0]
    data = parse(path, read_bytes(path))
    if client == "codex":
        data["model"] = "model-b"
    else:
        data["default_model"] = "vela/model-b"
    client_config.atomic_write(path, client_config.serialize(path, data))
    assert clients.plan(client).status == "Configured"
    catalog(config.parent, [model(), model("model-b"), model("new-model")])
    assert clients.sync_configured() == [f"{client_config.CLIENTS[client]}: models synced"]
    current = parse(path, path.read_bytes())
    field = {"codex": "model", "kimi": "default_model"}[client]
    assert current[field] == ("vela/model-b" if client == "kimi" else "model-b")


def test_codex_delete_restores_original_startup_fields_and_preserves_profiles(clients):
    config, models = clients.paths("codex")
    config.parent.mkdir(parents=True)
    config.write_text(
        'model="original-model"\nmodel_provider="original"\n'
        'model_catalog_json="C:/original-models.json"\nmodel_context_window=64000\n'
        '[model_providers.original]\nname="Original"\nbase_url="http://original"\n'
        '[mcp_servers.keep]\ncommand="keep"\n'
    )
    profile = config.parent / "work.config.toml"
    profile.write_text('model="work-model"\nmodel_provider="work"\n')
    profile_before = profile.read_bytes()
    old = parse(config, config.read_bytes())
    clients.apply(clients.plan("codex"))
    new = parse(config, config.read_bytes())
    assert new["model_provider"] == "vela"
    assert new["model"] == "model-a"
    assert "model_context_window" not in new
    # A second Apply and a model switch must not overwrite the restoration snapshot.
    new["model"] = "model-b"
    client_config.atomic_write(config, client_config.serialize(config, new))
    clients.apply(clients.plan("codex"))
    assert models.exists()
    clients.apply(clients.plan("codex", remove=True))
    restored = parse(config, config.read_bytes())
    for field in ("model", "model_provider", "model_catalog_json", "model_context_window"):
        assert restored[field] == old[field]
    assert restored["model_providers"]["original"] == old["model_providers"]["original"]
    assert restored["mcp_servers"] == old["mcp_servers"]
    assert "vela" not in restored["model_providers"]
    assert not models.exists()
    assert profile.read_bytes() == profile_before


def test_codex_delete_preserves_external_provider_change(clients):
    clients.apply(clients.plan("codex"))
    path = clients.paths("codex")[0]
    doc = parse(path, path.read_bytes())
    doc.update(model="external-model", model_provider="external", model_context_window=32000)
    client_config.atomic_write(path, client_config.serialize(path, doc))
    assert clients.plan("codex").status == "Needs review"
    clients.apply(clients.plan("codex", remove=True))
    current = parse(path, path.read_bytes())
    assert current["model"] == "external-model"
    assert current["model_provider"] == "external"
    assert current["model_context_window"] == 32000


def test_codex_sync_upgrades_legacy_reasoning_catalog(clients, configured, monkeypatch):
    _, config = configured
    efforts = ["low", "medium", "high", "xhigh", "max"]
    catalog(
        config.parent,
        [{**model(), "capabilities": {"supports": {"reasoning_effort": efforts}}}],
    )
    path, models = clients.paths("codex")
    path.parent.mkdir(parents=True)
    path.write_text('model_reasoning_effort = "high"\n')
    with monkeypatch.context() as patch:
        patch.setattr(
            type(clients),
            "codex_reasoning",
            staticmethod(lambda _: {"supported_reasoning_levels": []}),
        )
        clients.apply(clients.plan("codex"))
    assert clients.plan("codex").status == "Out of date"
    assert clients.sync_configured() == ["Codex: models synced"]
    result = json.loads(models.read_text(encoding="utf-8"))["models"][0]
    assert [item["effort"] for item in result["supported_reasoning_levels"]] == efforts
    assert result["default_reasoning_level"] == "medium"
    assert parse(path, path.read_bytes())["model_reasoning_effort"] == "high"
    assert clients.plan("codex").status == "Configured"
    assert clients.sync_configured() == []


def test_codex_sync_upgrades_legacy_image_capability(clients, configured, monkeypatch):
    _, config = configured
    catalog(
        config.parent,
        [
            {
                **model(),
                "capabilities": {
                    "supports": {"vision": True, "reasoning_effort": ["low", "medium", "high"]}
                },
            },
            {**model("text-only"), "capabilities": {"supports": {"vision": False}}},
        ],
    )
    with monkeypatch.context() as patch:
        patch.setattr(type(clients), "input_modalities", staticmethod(lambda _: ["text"]))
        clients.apply(clients.plan("codex", selected_models=["model-a"]))
    path, models = clients.paths("codex")
    config_before = path.read_bytes()
    before = json.loads(models.read_text(encoding="utf-8"))["models"]
    assert clients.plan("codex").status == "Out of date"
    assert clients.sync_configured() == ["Codex: models synced"]
    after = json.loads(models.read_text(encoding="utf-8"))["models"]
    assert len(after) == 1
    assert after[0] == {**before[0], "input_modalities": ["text", "image"]}
    assert path.read_bytes() == config_before
    assert clients.plan("codex").status == "Configured"


def test_kimi_configure_preserves_defaults_delete_clears_only_owned_references(clients):
    target = clients.paths("kimi")[0]
    target.parent.mkdir(parents=True)
    target.write_text(
        'default_model="other"\n[models.other]\nprovider="other"\n'
        'model="other-model"\nmax_context_size=10000\n'
        '[providers.other]\ntype="openai_legacy"\napi_key="other-secret"\n'
        'base_url="http://other"\n'
    )
    auth = target.parent / "auth.json"
    auth.write_text('{"other":{"apiKey":"private"}}')
    auth_before = auth.read_bytes()
    clients.apply(clients.plan("kimi"))
    current = parse(target, target.read_bytes())
    assert current["default_model"] == "other"
    current["default_model"] = "vela/model-b"
    client_config.atomic_write(target, client_config.serialize(target, current))
    clients.apply(clients.plan("kimi", remove=True))
    current = parse(target, target.read_bytes())
    assert current["default_model"] == ""
    assert current["models"]["other"]["model"] == "other-model"
    assert current["providers"]["other"]["api_key"] == "other-secret"
    assert auth.read_bytes() == auth_before


def test_codex_remove_failure_rolls_back_and_preserves_registration(clients, monkeypatch):
    from pathlib import Path

    clients.apply(clients.plan("codex"))
    plan = clients.plan("codex", remove=True)
    before = {c.path: c.before for c in plan.changes}
    # Codex removes its catalog file; emulate a failure removing the state record.
    unlink = Path.unlink

    def fail_unlink(path, *args, **kwargs):
        if path == plan.state_path:
            raise OSError("failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(ValueError, match="Unable to save"):
        clients.apply(plan)
    assert {p: read_bytes(p) for p in before} == before
    assert read_bytes(plan.state_path) == plan.state_before


@pytest.mark.parametrize("client", ["codex", "kimi"])
def test_empty_selection_never_writes_and_state_contains_no_credentials(clients, client):
    plan = clients.plan(client, selected_models=[])
    assert plan.error
    with pytest.raises(ValueError):
        clients.apply(plan)
    assert not any(path.exists() for path in clients.paths(client))
    clients.apply(clients.plan(client))
    state = (clients.state_dir / f"{client}.json").read_text()
    assert clients.settings.local_api_key not in state
    assert json.loads(state)["selected_models"] is None


@pytest.mark.parametrize("client", ["dsh", "codex", "kimi"])
def test_delete_orphaned_registration_does_not_recreate_files(clients, client):
    clients.apply(clients.plan(client))
    for path in clients.paths(client):
        path.unlink(missing_ok=True)
    plan = clients.plan(client, remove=True)
    clients.apply(plan)
    assert not plan.state_path.exists()
    assert not any(path.exists() for path in clients.paths(client))
    assert clients.sync_configured() == []
