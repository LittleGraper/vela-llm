from __future__ import annotations

import json

import pytest
from test_model_context import catalog, model
from test_model_context import configured as configured

import client_config
from client_config import CLIENTS, ClientManager, parse
from model_store import write_context, write_default_model


@pytest.fixture
def clients(configured, tmp_path):
    settings, _ = configured
    home = tmp_path / "home"
    return ClientManager(settings, home=home, environ={"KIMI_SHARE_DIR": str(home / ".kimi")})


@pytest.mark.parametrize("client", CLIENTS)
def test_apply_preserves_unrelated_settings_and_status_lifecycle(clients, client):
    paths = clients.paths(client)
    paths[0].parent.mkdir(parents=True)
    if paths[0].suffix == ".toml":
        original = '# keep me\n[other]\nvalue = "preserved"\n'
    elif paths[0].suffix == ".yaml":
        original = "# keep me\nother:\n  value: preserved\n"
    else:
        original = '{"other": {"value": "preserved"}}'
    paths[0].write_text(original, encoding="utf-8")
    original_bytes = paths[0].read_bytes()
    plan = clients.plan(client)
    assert plan.status == "Not configured" and not plan.error
    for change in plan.changes:
        preview, _ = change.preview()
        assert clients.settings.local_api_key not in preview
    assert paths[0].read_text(encoding="utf-8") == original
    assert not clients.state_dir.exists()
    clients.apply(plan)
    assert parse(paths[0], paths[0].read_bytes())["other"]["value"] == "preserved"
    if paths[0].suffix != ".json":
        assert "# keep me" in paths[0].read_text(encoding="utf-8")
    assert clients.settings.local_api_key not in plan.state_path.read_text()
    assert list((clients.state_dir / "backups").glob("*/0"))[0].read_bytes() == original_bytes
    updated = clients.plan(client)
    assert updated.status == "Configured" and not updated.changed and not updated.error
    clients.settings.port += 1
    updated = clients.plan(client)
    assert updated.status == "Out of date"
    clients.apply(updated)
    assert clients.plan(client).status == "Configured"
    paths[0].write_bytes(paths[0].read_bytes().replace(b"http://127.0.0.1", b"http://external"))
    # Kimi stores model-a in models.vela too, so all adapters detect the drift.
    assert clients.plan(client).status == "Needs review"


def test_native_client_contracts(clients):
    for client in CLIENTS:
        clients.apply(clients.plan(client))

    def read(client, index=0):
        path = clients.paths(client)[index]
        return parse(path, path.read_bytes())

    codex = read("codex")
    assert codex["model_provider"] == "vela"
    assert codex["model_providers"]["vela"]["wire_api"] == "responses"
    assert codex["model_providers"]["vela"]["experimental_bearer_token"] == "test-key"
    assert read("kimi")["models"]["vela/model-a"]["max_context_size"] == 272000
    assert read("kimi")["providers"]["vela"]["type"] == "openai_legacy"
    assert "agent-default-model" not in read("dsh")
    assert read("dsh", 1)["version"] == 1
    assert read("dsh", 1)["refs"]["VELA_API_KEY"] == "test-key"
    preview = clients.plan("codex").changes[1].preview()[0]
    assert "Bundled Codex fallback prompt" in preview
    assert len(preview) < 5000


@pytest.mark.parametrize("versioned", [True, False])
def test_dsh_credentials_preserve_existing_format_records_and_comments(clients, versioned):
    path = clients.paths("dsh")[1]
    path.parent.mkdir(parents=True)
    original = (
        "version: 1\nrefs:\n  # keep reference\n  OTHER_API_KEY: other-secret\n"
        "records:\n  llm-pi-ai/openai-codex:\n    kind: grant\n"
        "    payload:\n      access: access-secret\n      refresh: refresh-secret\n"
        if versioned
        else "# keep reference\nOTHER_API_KEY: other-secret\n"
    )
    path.write_text(original, encoding="utf-8")
    before = parse(path, path.read_bytes())
    plan = clients.plan("dsh")
    assert not plan.error
    preview = plan.changes[1].preview_diff(clients.settings.local_api_key)[0]
    for secret in ("test-key", "other-secret", "access-secret", "refresh-secret"):
        assert secret not in preview
    clients.apply(plan)
    after = parse(path, path.read_bytes())
    refs = after["refs"] if versioned else after
    assert refs["VELA_API_KEY"] == "test-key" and refs["OTHER_API_KEY"] == "other-secret"
    assert "# keep reference" in path.read_text()
    if versioned:
        assert after["records"] == before["records"]
        assert set(after) == {"version", "refs", "records"}
    else:
        assert "version" not in after
    assert clients.plan("dsh").status == "Configured"


@pytest.mark.parametrize("contents", ["version: 1\n", "version: 1\nrefs: null\nrecords: null\n"])
def test_dsh_empty_versioned_sections(clients, contents):
    path = clients.paths("dsh")[1]
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    clients.apply(clients.plan("dsh"))
    assert parse(path, path.read_bytes())["refs"]["VELA_API_KEY"] == "test-key"
    assert clients.plan("dsh").status == "Configured"


@pytest.mark.parametrize(
    "contents",
    [
        "version: 2\nrefs: {}\n",
        'version: "1"\n',
        "version: 1\nrefs: secret\n",
        "version: 1\nrecords: [secret]\n",
        "version: 1\nrefs:\n  KEY: 123\n",
        'version: 1\nrefs:\n  KEY: ""\n',
        "version: 1\nextra: secret\n",
        "KEY: 123\n",
    ],
)
def test_dsh_invalid_credential_structure_blocks_without_leaking(clients, contents):
    path = clients.paths("dsh")[1]
    path.parent.mkdir(parents=True)
    path.write_text(contents)
    before = path.read_bytes()
    plan = clients.plan("dsh")
    assert plan.error and plan.status == "Needs review"
    assert "secret" not in plan.error
    with pytest.raises(ValueError):
        clients.apply(plan)
    assert path.read_bytes() == before


@pytest.mark.parametrize("client", CLIENTS)
def test_stale_preview_cannot_overwrite_external_edits(clients, client):
    plan = clients.plan(client)
    path = clients.paths(client)[0]
    path.parent.mkdir(parents=True)
    path.write_text("external edit", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after"):
        clients.apply(plan)
    assert path.read_text() == "external edit"
    assert not plan.state_path.exists()


@pytest.mark.parametrize("client", ["dsh", "codex"])
@pytest.mark.parametrize("existing", [True, False])
def test_multifile_failure_restores_exact_original_bytes(clients, monkeypatch, client, existing):
    first, second = clients.paths(client)
    original = (
        b"\xef\xbb\xbfother: kept\r\n" if client == "dsh" else b'\xef\xbb\xbfother = "kept"\r\n'
    )
    if existing:
        first.parent.mkdir(parents=True)
        first.write_bytes(original)
    plan = clients.plan(client)
    write = client_config.atomic_write

    def fail_second(path, contents):
        if path == second:
            raise OSError("test failure")
        write(path, contents)

    monkeypatch.setattr(client_config, "atomic_write", fail_second)
    with pytest.raises(ValueError, match="Unable to save"):
        clients.apply(plan)
    assert first.read_bytes() == original if existing else not first.exists()
    assert not second.exists() and not plan.state_path.exists()


@pytest.mark.parametrize(
    "client,bad",
    [
        ("codex", 'api_key = "private"\napi_key = "secret"'),
        ("kimi", 'providers = "secret"'),
        ("dsh", "token: secret\ntoken: private"),
    ],
)
def test_invalid_configuration_blocks_without_disclosing_secrets(clients, client, bad):
    path = clients.paths(client)[0]
    path.parent.mkdir(parents=True)
    path.write_text(bad, encoding="utf-8")
    plan = clients.plan(client)
    assert plan.error and plan.status == "Needs review"
    assert "secret" not in plan.error and "private" not in plan.error
    with pytest.raises(ValueError):
        clients.apply(plan)
    assert path.read_text() == bad


def test_vela_default_changes_do_not_change_client_selection(clients, configured):
    _, path = configured
    clients.apply(clients.plan("codex"))
    clients.apply(clients.plan("kimi", selected_models=["model-b"]))
    write_default_model(path, "model-b")
    assert clients.plan("codex").status == "Configured"
    file = clients.paths("codex")[0]
    assert parse(file, file.read_bytes())["model"] == "model-a"
    assert clients.plan("kimi").selected_models == ["model-b"]
    write_context(path, "model-b", "maximum")
    assert clients.plan("kimi").status == "Out of date"
    plan = clients.plan("kimi")
    clients.settings.local_api_key = "another-key"
    with pytest.raises(ValueError, match="VELA settings changed"):
        clients.apply(plan)


def test_codex_unrelated_legacy_profiles_are_preserved(clients):
    path = clients.paths("codex")[0]
    path.parent.mkdir(parents=True)
    path.write_text(
        'profile = "work"\n[profiles.work]\nmodel = "old"\nsandbox_mode = "read-only"\n'
    )
    clients.apply(clients.plan("codex"))
    data = parse(path, path.read_bytes())
    assert data["profiles"]["work"]["model"] == "old"
    assert data["profiles"]["work"]["sandbox_mode"] == "read-only"
    path.write_text(path.read_text().replace("read-only", "workspace-write"))
    assert clients.plan("codex").status == "Configured"


def test_environment_paths_and_dsh_key_conflict(clients, tmp_path):
    variables = {
        "dsh": "DSH_HOME",
        "codex": "CODEX_HOME",
        "kimi": "KIMI_SHARE_DIR",
    }
    for client, variable in variables.items():
        clients.environ[variable] = str(tmp_path / client)
        assert clients.paths(client)[0].parent == tmp_path / client
    clients.environ["VELA_API_KEY"] = "different"
    assert clients.plan("dsh").error


def test_capability_filter_and_missing_context(clients, configured):
    settings, path = configured
    entry = model()
    entry["supported_endpoints"] = ["/chat/completions"]
    catalog(path.parent, [entry])
    assert clients.models("codex") == []
    assert clients.plan("codex").error
    catalog(path.parent, [{"name": "model-a", "upstream": "github_copilot/model-a"}])
    assert clients.plan("kimi").error


def test_kimi_includes_models_with_input_limit_but_no_default_tier(clients, configured):
    _, path = configured
    entry = {"name": "model-b", "upstream": "github_copilot/model-b", "max_input_tokens": 128000}
    catalog(path.parent, [model(), entry])
    plan = clients.plan("kimi")
    assert plan.catalog_count == 2 and not plan.error
    clients.apply(plan)
    file = clients.paths("kimi")[0]
    assert parse(file, file.read_bytes())["models"]["vela/model-b"]["max_context_size"] == 128000


def test_parallel_vela_state_and_unmodified_bom(clients):
    first = clients.plan("codex")
    second = clients.plan("codex")
    clients.apply(first)
    with pytest.raises(ValueError):
        clients.apply(second)
    path = clients.paths("codex")[0]
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert not clients.plan("codex").changed


def test_yaml_aliases_do_not_mutate_other_sections(clients):
    path = clients.paths("dsh")[0]
    path.parent.mkdir(parents=True)
    path.write_text(
        "other: &original\n  provider: old\n  model: old\nagent-default-model: *original\n"
    )
    clients.apply(clients.plan("dsh"))
    data = parse(path, path.read_bytes())
    assert data["other"] == {"provider": "old", "model": "old"}
    assert data["agent-default-model"]["model"] == "old"


def test_preview_masks_old_and_new_keys_headers_and_comments(clients):
    path = clients.paths("codex")[0]
    path.parent.mkdir(parents=True)
    path.write_text(
        "# private-comment\n[model_providers.vela]\n"
        'experimental_bearer_token = "old-secret"\n'
        '[model_providers.vela.http_headers]\nAuthorization = "private-header"\n'
    )
    plan = clients.plan("codex")
    preview, syntax = plan.changes[0].preview_diff(clients.settings.local_api_key)
    assert syntax == "diff"
    for secret in ("test-key", "old-secret", "private-comment", "private-header"):
        assert secret not in preview
    assert "After apply" in preview and "model-a" in preview


def test_codex_catalog_preserves_image_input_capability(clients):
    catalog = clients.codex_catalog(
        [
            {**model("vision"), "capabilities": {"input_modalities": ["text", "image"]}},
            {**model("text")},
        ]
    )
    assert catalog[0]["input_modalities"] == ["text", "image"]
    assert catalog[1]["input_modalities"] == ["text"]


@pytest.mark.parametrize(
    "capabilities,expected",
    [
        ({"supports": {"vision": True}}, ["text", "image"]),
        ({"supports": {"vision": False}}, ["text"]),
        ({"supports": {"vision": "true"}}, ["text"]),
        ({"supports": {"vision": 1}}, ["text"]),
        ({"supports": {}}, ["text"]),
        ({"supports": None}, ["text"]),
        ({"supports": []}, ["text"]),
        ({"vision": True}, ["text", "image"]),
        ({"image_input": True}, ["text", "image"]),
        ({"input_modalities": ["text", "image", "audio"]}, ["text", "image"]),
        ({}, ["text"]),
        (None, ["text"]),
    ],
)
def test_codex_catalog_maps_copilot_vision_capability(clients, capabilities, expected):
    result = clients.codex_catalog([{**model(), "capabilities": capabilities}])[0]
    assert result["input_modalities"] == expected


@pytest.mark.parametrize(
    "efforts,default",
    [
        (["low", "medium", "high", "xhigh", "max"], "medium"),
        (["none", "low", "medium", "high", "xhigh", "max"], "medium"),
        (["minimal", "low", "high"], "minimal"),
        (["high"], "high"),
        (["none"], "none"),
        (["medium", "medium", "high"], "medium"),
        ([], None),
    ],
)
def test_codex_catalog_maps_advertised_reasoning_levels(clients, efforts, default):
    entry = {
        **model("reasoner"),
        "capabilities": {"supports": {"reasoning_effort": efforts}},
    }
    result = clients.codex_catalog([entry])[0]
    assert [level["effort"] for level in result["supported_reasoning_levels"]] == list(
        dict.fromkeys(efforts)
    )
    assert all(level["description"] for level in result["supported_reasoning_levels"])
    if default is None:
        assert "default_reasoning_level" not in result
    else:
        assert result["default_reasoning_level"] == default


def test_codex_catalog_does_not_guess_reasoning_from_model_name(clients):
    result = clients.codex_catalog([model("gpt-6-astra")])[0]
    assert result["supported_reasoning_levels"] == []
    assert "default_reasoning_level" not in result


@pytest.mark.parametrize("efforts", [True, "high", {}, [None], ["unknown"]])
def test_codex_invalid_reasoning_metadata_blocks_configuration(clients, configured, efforts):
    _, config = configured
    catalog(
        config.parent,
        [{**model(), "capabilities": {"supports": {"reasoning_effort": efforts}}}],
    )
    plan = clients.plan("codex")
    assert "reasoning efforts" in plan.error
    with pytest.raises(ValueError, match="reasoning efforts"):
        clients.apply(plan)
    assert not any(path.exists() for path in clients.paths("codex"))


def test_live_settings_reload_detects_key_rotation(clients):
    live = clients.settings.model_copy()
    clients.settings_loader = lambda: live
    plan = clients.plan("codex")
    live.local_api_key = "rotated"
    with pytest.raises(ValueError, match="VELA settings changed"):
        clients.apply(plan)
    assert not clients.paths("codex")[0].exists()
    clients.apply(clients.plan("codex"))
    live.local_api_key = "rotated-again"
    assert clients.plan("codex").status == "Out of date"


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_protocol_selection_and_saved_configuration(clients, configured, client, prefix):
    _, path = configured
    entries = [
        {**model("chat"), "supported_endpoints": [prefix + "/chat/completions"]},
        {**model("responses"), "supported_endpoints": [prefix + "/responses"]},
        {
            **model("both"),
            "supported_endpoints": [prefix + "/responses", prefix + "/chat/completions"],
        },
        {**model("embedding"), "mode": "embedding", "supported_endpoints": ["/embeddings"]},
        {**model("websocket"), "supported_endpoints": ["ws:/responses"]},
        {**model("other"), "supported_endpoints": ["/messages"]},
        {**model("legacy-responses"), "mode": "responses"},
    ]
    catalog(path.parent, entries)
    expected = (
        ["responses", "both", "legacy-responses"]
        if client == "codex"
        else ["chat", "responses", "both", "legacy-responses"]
    )
    assert [e["name"] for e in clients.models(client)] == expected
    for name in expected:
        plan = clients.plan(client)
        assert not plan.error
        clients.apply(plan)
        file = clients.paths(client)[0]
        saved = parse(file, file.read_bytes())
        if client == "codex":
            assert saved["model_providers"]["vela"]["wire_api"] == "responses"
        elif client == "kimi":
            provider = saved["models"]["vela/" + name]["provider"]
            assert provider == ("vela-chat" if name == "chat" else "vela")
            assert saved["providers"][provider]["type"] == (
                "openai_legacy" if name == "chat" else "openai_responses"
            )
        elif client == "dsh":
            assert "agent-default-model" not in saved
            provider = "vela-chat" if name == "chat" else "vela"
            assert saved["llm-pi-ai"]["providers"][provider]["api"] == (
                "openai-completions" if name == "chat" else "openai-responses"
            )
        assert clients.plan(client).status == "Configured"


@pytest.mark.parametrize("client", ["dsh", "kimi"])
def test_protocol_metadata_changes_mark_out_of_date_and_reject_stale_plan(
    clients, configured, client
):
    _, path = configured
    entry = {**model(), "supported_endpoints": ["/chat/completions"]}
    catalog(path.parent, [entry])
    clients.apply(clients.plan(client))
    stale = clients.plan(client)
    entry["supported_endpoints"] = ["/responses"]
    catalog(path.parent, [entry])
    plan = clients.plan(client)
    assert plan.status == "Out of date" and not plan.error
    assert "openai" in plan.changes[0].preview_diff(clients.settings.local_api_key)[0]
    with pytest.raises(ValueError, match="VELA settings changed"):
        clients.apply(stale)
    clients.apply(plan)
    assert clients.plan(client).status == "Configured"


def configured_model_names(clients, client):
    paths = clients.paths(client)
    data = parse(paths[0], paths[0].read_bytes())
    if client == "codex":
        return [e["slug"] for e in parse(paths[1], paths[1].read_bytes())["models"]]
    if client == "kimi":
        return [
            value["model"] for name, value in data["models"].items() if name.startswith("vela/")
        ]
    providers = data["llm-pi-ai"]["providers"]
    return [
        entry["id"]
        for name, provider in providers.items()
        if name in ("vela", "vela-chat")
        for entry in provider["models"]
    ]


@pytest.mark.parametrize("client", CLIENTS)
def test_whole_catalog_sync_add_remove_context_and_stable_default(clients, configured, client):
    _, path = configured
    clients.apply(clients.plan(client))
    assert set(configured_model_names(clients, client)) == {"model-a", "model-b"}
    entries = [model(), model("model-c", default=64000)]
    catalog(path.parent, entries)
    assert clients.plan(client).status == "Out of date"
    result = clients.sync_configured()
    assert result == [f"{CLIENTS[client]}: models synced"]
    assert set(configured_model_names(clients, client)) == {"model-a", "model-c"}
    assert clients.plan(client).selected_models is None
    assert clients.plan(client).status == "Configured"
    # Reordered upstream results do not cause writes or backups.
    before = {p: client_config.read_bytes(p) for p in clients.paths(client)}
    catalog(path.parent, entries[::-1])
    assert clients.sync_configured() == []
    assert before == {p: client_config.read_bytes(p) for p in clients.paths(client)}
    entries[1]["default_context_size"] = 128000
    catalog(path.parent, entries)
    assert clients.sync_configured() == [f"{CLIENTS[client]}: models synced"]


def test_auto_sync_skips_unregistered_and_externally_edited_clients(clients, configured):
    _, path = configured
    clients.apply(clients.plan("dsh"))
    clients.apply(clients.plan("kimi"))
    dsh = clients.paths("dsh")[0]
    dsh.write_bytes(dsh.read_bytes().replace(b"displayName: VELA", b"displayName: My VELA"))
    original = dsh.read_bytes()
    catalog(path.parent, [model(), model("model-c")])
    result = clients.sync_configured()
    assert result == ["DSH: Needs review (open /clients)", "Kimi: models synced"]
    assert dsh.read_bytes() == original
    assert not any(p.exists() for p in clients.paths("codex"))


@pytest.mark.parametrize("client", ["codex", "kimi"])
def test_unavailable_selected_catalog_requires_review_without_partial_sync(
    clients, configured, client
):
    _, path = configured
    clients.apply(clients.plan(client, selected_models=["model-b"]))
    before = [client_config.read_bytes(p) for p in clients.paths(client)]
    catalog(path.parent, [model()])
    assert clients.sync_configured() == [f"{CLIENTS[client]}: Needs review (open /clients)"]
    assert before == [client_config.read_bytes(p) for p in clients.paths(client)]


def test_kimi_preserves_unrelated_entries_and_detects_new_owned_name_conflicts(clients, configured):
    _, path = configured
    target = clients.paths("kimi")[0]
    target.parent.mkdir(parents=True)
    target.write_text(
        '[providers.other]\ntype="openai_legacy"\nbase_url="http://other"\n'
        'api_key="other-secret"\n[models.other]\nprovider="other"\n'
        'model="other"\nmax_context_size=100000\n'
    )
    clients.apply(clients.plan("kimi"))
    catalog(path.parent, [model(), model("model-c")])
    assert clients.sync_configured() == ["Kimi: models synced"]
    data = parse(target, target.read_bytes())
    assert data["providers"]["other"]["api_key"] == "other-secret"
    assert data["models"]["other"]["model"] == "other"
    assert "vela/model-b" not in data["models"]
    with target.open("a") as stream:
        stream.write(
            '\n[models."vela/manual"]\nprovider="vela"\nmodel="manual"\nmax_context_size=100000\n'
        )
    assert clients.sync_configured() == ["Kimi: Needs review (open /clients)"]
    assert "vela/manual" in target.read_text()


def test_kimi_and_dsh_group_all_dual_protocol_models_under_vela(clients, configured):
    _, path = configured
    entries = [
        {**model(), "supported_endpoints": ["/responses"]},
        {**model("model-b"), "supported_endpoints": ["/responses", "/chat/completions"]},
    ]
    catalog(path.parent, entries)
    for client in ("dsh", "kimi"):
        clients.apply(clients.plan(client))
        target = clients.paths(client)[0]
        data = parse(target, target.read_bytes())
        providers = data["llm-pi-ai"]["providers"] if client == "dsh" else data["providers"]
        assert set(providers) == {"vela"}
        assert set(configured_model_names(clients, client)) == {"model-a", "model-b"}


def test_cli_refresh_syncs_only_after_success(clients, configured, monkeypatch):
    import cli
    import github_copilot_models

    settings, path = configured
    clients.apply(clients.plan("kimi"))
    monkeypatch.setattr(client_config, "ClientManager", lambda _: clients)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        github_copilot_models, "fetch_available_models", lambda: [model(), model("c")]
    )
    assert cli.refresh_model_cache(path.parent)
    assert set(configured_model_names(clients, "kimi")) == {"model-a", "c"}
    before = [client_config.read_bytes(p) for p in clients.paths("kimi")]
    monkeypatch.setattr(github_copilot_models, "fetch_available_models", lambda: [])
    assert not cli.refresh_model_cache(path.parent)
    assert before == [client_config.read_bytes(p) for p in clients.paths("kimi")]


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("external_edit", [False, True])
def test_upgrade_single_model_record_without_overwriting_edits(clients, client, external_edit):
    # Reproduce the previous adapter's exact field fingerprints and single-model files.
    paths = clients.paths(client)
    key = clients.settings.local_api_key
    provider = {
        "name": "VELA",
        "base_url": "http://127.0.0.1:4000/v1",
        "wire_api": "responses",
        "experimental_bearer_token": key,
        "requires_openai_auth": False,
    }
    documents = {
        "codex": [
            {
                "model": "model-a",
                "model_provider": "vela",
                "model_context_window": 272000,
                "model_providers": {"vela": provider},
            }
        ],
        "kimi": [
            {
                "default_model": "vela",
                "providers": {
                    "vela": {
                        "type": "openai_legacy",
                        "base_url": "http://127.0.0.1:4000/v1",
                        "api_key": key,
                    }
                },
                "models": {
                    "vela": {"model": "model-a", "provider": "vela", "max_context_size": 272000}
                },
            }
        ],
        "dsh": [
            {
                "llm-pi-ai": {
                    "providers": {
                        "vela": {
                            "displayName": "VELA",
                            "api": "openai-completions",
                            "baseURL": "http://127.0.0.1:4000/v1",
                            "apiKeyEnv": "VELA_API_KEY",
                            "models": [{"id": "model-a", "contextWindow": 272000}],
                        }
                    }
                },
                "agent-default-model": {"provider": "vela", "model": "model-a"},
            },
            {"version": 1, "refs": {"VELA_API_KEY": key}},
        ],
    }[client]
    for path, document in zip(paths, documents, strict=False):
        client_config.atomic_write(path, client_config.serialize(path, document))
    keys = {
        "codex": [
            [["model"], ["model_provider"], ["model_context_window"], ["model_providers", "vela"]]
        ],
        "kimi": [[["default_model"], ["providers", "vela"], ["models", "vela"]]],
        "dsh": [
            [
                ["llm-pi-ai", "providers", "vela"],
                ["agent-default-model", "provider"],
                ["agent-default-model", "model"],
                ["agent-default-model", "reasoningEffort"],
            ],
            [["version"], ["refs", "VELA_API_KEY"]],
        ],
    }[client]
    fingerprint = {
        str(path): {json.dumps(k): client_config.get_value(doc, k) for k in fields}
        for path, doc, fields in zip(paths, documents, keys, strict=False)
    }
    client_config.atomic_write(
        clients.state_dir / f"{client}.json",
        json.dumps(
            {
                "model": "model-a",
                "follows_default": True,
                "managed_digest": client_config.digest(fingerprint),
            }
        ),
    )
    if external_edit:
        paths[0].write_bytes(paths[0].read_bytes().replace(b"model-a", b"manual-model"))
        assert clients.sync_configured() == [f"{CLIENTS[client]}: Needs review (open /clients)"]
        assert b"manual-model" in paths[0].read_bytes()
    else:
        assert clients.sync_configured() == [f"{CLIENTS[client]}: models synced"]
        assert set(configured_model_names(clients, client)) == {"model-a", "model-b"}
