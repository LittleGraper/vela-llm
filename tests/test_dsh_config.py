import json

import pytest
from test_client_config import clients as clients
from test_client_config import configured_model_names
from test_model_context import catalog, model
from test_model_context import configured as configured

import client_config
from client_config import parse


@pytest.mark.parametrize("display_name", [None, "", " ", 42, " GPT 6 Astra "])
def test_dsh_model_display_name_uses_upstream_name_or_formatted_id(clients, display_name):
    entry = {**model("gpt-6-astra"), "display_name": display_name}
    info = clients.model_info(entry)
    assert info["id"] == "gpt-6-astra"
    assert info["name"] == ("GPT 6 Astra" if display_name == " GPT 6 Astra " else "GPT-6 Astra")


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("gpt-6-astra", "GPT-6 Astra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("gpt-5.3-codex", "GPT-5.3 Codex"),
        ("gpt-5-mini", "GPT-5 Mini"),
        ("deepseek-chat", "Deepseek Chat"),
        ("model", "Model"),
    ],
)
def test_dsh_missing_display_name_is_formatted(clients, model_id, expected):
    info = clients.model_info(model(model_id))
    assert info["name"] == expected
    assert info["id"] == model_id


def test_dsh_sync_refreshes_display_name_without_switching_models(clients, configured):
    _, config = configured
    clients.apply(clients.plan("dsh", selected_models=["model-a"]))
    path = clients.paths("dsh")[0]
    doc = parse(path, path.read_bytes())
    doc["agent-default-model"] = {"provider": "vela", "model": "model-a"}
    client_config.atomic_write(path, client_config.serialize(path, doc))
    catalog(config.parent, [{**model(), "display_name": "Friendly model name"}, model("model-b")])
    assert clients.sync_configured() == ["DSH: models synced"]
    doc = parse(path, path.read_bytes())
    models = doc["llm-pi-ai"]["providers"]["vela"]["models"]
    assert len(models) == 1
    assert models[0]["id"] == "model-a"
    assert models[0]["name"] == "Friendly model name"
    assert doc["agent-default-model"] == {"provider": "vela", "model": "model-a"}
    assert clients.plan("dsh").status == "Configured"


def test_dsh_all_models_preserves_web_selection_across_configure_and_refresh(clients, configured):
    _, config = configured
    path = clients.paths("dsh")[0]
    path.parent.mkdir(parents=True)
    path.write_text(
        "agent-default-model:\n  provider: deepseek\n  model: deepseek-chat\n"
        "  reasoningEffort: high\n"
    )
    clients.apply(clients.plan("dsh"))
    assert set(configured_model_names(clients, "dsh")) == {"model-a", "model-b"}
    default = parse(path, path.read_bytes())["agent-default-model"]
    assert default == {"provider": "deepseek", "model": "deepseek-chat", "reasoningEffort": "high"}
    # Simulate switching inside the DSH Web UI. This is not VELA-owned state.
    path.write_text(
        path.read_text()
        .replace("deepseek-chat", "model-b")
        .replace("provider: deepseek", "provider: vela")
    )
    assert clients.plan("dsh").status == "Configured"
    catalog(config.parent, [model(), model("model-b"), model("new-model")])
    assert clients.sync_configured() == ["DSH: models synced"]
    assert set(configured_model_names(clients, "dsh")) == {"model-a", "model-b", "new-model"}
    assert parse(path, path.read_bytes())["agent-default-model"]["model"] == "model-b"


def test_dsh_subset_persists_and_does_not_add_new_models(clients, configured):
    _, config = configured
    plan = clients.plan("dsh", selected_models=["model-b"])
    assert not clients.paths("dsh")[0].exists()
    clients.apply(plan)
    assert clients.plan("dsh").selected_models == ["model-b"]
    catalog(config.parent, [model(), model("model-b"), model("new-model")])
    assert clients.sync_configured() == []
    assert configured_model_names(clients, "dsh") == ["model-b"]
    clients.apply(clients.plan("dsh", selected_models=None))
    assert clients.plan("dsh").selected_models is None
    assert len(configured_model_names(clients, "dsh")) == 3


def test_empty_selection_blocks_configure_but_allows_delete(clients):
    clients.apply(clients.plan("dsh"))
    plan = clients.plan("dsh", selected_models=[])
    assert "at least one" in plan.error
    with pytest.raises(ValueError):
        clients.apply(plan)
    assert not clients.plan("dsh", remove=True).error


@pytest.mark.parametrize("default_provider", ["vela", "deepseek"])
def test_delete_only_vela_and_stop_sync(clients, configured, default_provider):
    _, config = configured
    settings, credentials = clients.paths("dsh")
    settings.parent.mkdir(parents=True)
    settings.write_text(
        "other: kept\nagent-default-model:\n  provider: " + default_provider + "\n"
        "  model: example\n  reasoningEffort: high\n"
        "  extra: kept\nllm-pi-ai:\n  providers:\n    other:\n      api: openai-responses\n"
    )
    credentials.write_text(
        "version: 1\nrefs:\n  OTHER_API_KEY: other-secret\nrecords:\n"
        "  llm-pi-ai/openai-codex:\n    kind: grant\n    payload: {access: access-secret}\n"
    )
    clients.apply(clients.plan("dsh"))
    original = [p.read_bytes() for p in (settings, credentials)]
    removal = clients.plan("dsh", remove=True)
    clients.apply(removal)
    result = parse(settings, settings.read_bytes())
    assert "vela" not in result["llm-pi-ai"]["providers"]
    assert result["llm-pi-ai"]["providers"]["other"]["api"] == "openai-responses"
    assert result["other"] == "kept"
    if default_provider == "vela":
        assert result["agent-default-model"] == {"extra": "kept"}
    else:
        assert result["agent-default-model"]["provider"] == "deepseek"
    creds = parse(credentials, credentials.read_bytes())
    assert creds["refs"] == {"OTHER_API_KEY": "other-secret"}
    assert creds["records"]["llm-pi-ai/openai-codex"]["payload"]["access"] == "access-secret"
    assert not removal.state_path.exists()
    assert clients.plan("dsh").status == "Not configured"
    assert not clients.plan("dsh", remove=True).has_configuration
    catalog(config.parent, [model(), model("new")])
    assert clients.sync_configured() == []
    backups = list((clients.state_dir / "backups").glob("*/state.json"))
    assert len(backups) == 1
    assert (backups[0].parent / "0").read_bytes() == original[0]
    assert (backups[0].parent / "1").read_bytes() == original[1]


def test_delete_preserves_shared_credential(clients):
    clients.apply(clients.plan("dsh"))
    path = clients.paths("dsh")[0]
    with path.open("a") as stream:
        stream.write("other-adapter:\n  apiKeyEnv: VELA_API_KEY\n")
    clients.apply(clients.plan("dsh", remove=True))
    creds = clients.paths("dsh")[1]
    assert parse(creds, creds.read_bytes())["refs"]["VELA_API_KEY"] == "test-key"
    assert not clients.plan("dsh", remove=True).has_configuration


def test_delete_stale_plan_and_partial_failure_leave_originals(clients, monkeypatch):
    clients.apply(clients.plan("dsh"))
    plan = clients.plan("dsh", remove=True)
    before = [c.before for c in plan.changes]
    write = client_config.atomic_write

    def fail(path, content):
        if path == clients.paths("dsh")[1]:
            raise OSError("simulated failure")
        write(path, content)

    monkeypatch.setattr(client_config, "atomic_write", fail)
    with pytest.raises(ValueError):
        clients.apply(plan)
    assert [p.read_bytes() for p in clients.paths("dsh")] == before
    assert plan.state_path.read_bytes() == plan.state_before
    monkeypatch.setattr(client_config, "atomic_write", write)
    path = clients.paths("dsh")[0]
    path.write_bytes(path.read_bytes() + b"\n# external edit\n")
    with pytest.raises(ValueError, match="changed after"):
        clients.apply(plan)


def test_removed_selected_models_retained_as_preferences(clients, configured):
    _, config = configured
    clients.apply(clients.plan("dsh", selected_models=["model-b"]))
    catalog(config.parent, [model()])
    assert clients.plan("dsh").error
    assert clients.sync_configured() == ["DSH: Needs review (open /clients)"]
    saved = json.loads((clients.state_dir / "dsh.json").read_text())
    assert saved["selected_models"] == ["model-b"]
