import json
import os
import subprocess

import pytest
from test_client_updates import capable
from test_model_context import catalog, model
from test_model_context import configured as configured

from client_config import ClientManager, parse


@pytest.mark.parametrize("layout", ["new", "legacy", "both", "migrated"])
def test_kimi_path_detects_current_and_legacy_data(configured, tmp_path, layout):
    settings, _ = configured
    home = tmp_path / "home"
    old = home / ".kimi"
    new = home / ".kimi-code"
    if layout in ("legacy", "both", "migrated"):
        old.mkdir(parents=True)
        (old / "config.toml").write_text("", encoding="utf-8")
    if layout == "both":
        new.mkdir()
    if layout == "migrated":
        (old / ".migrated-to-kimi-code").write_text("", encoding="utf-8")
    manager = ClientManager(settings, home=home, environ={})
    assert manager.paths("kimi") == [(old if layout == "legacy" else new) / "config.toml"]
    assert manager.kimi_layout()[1] is (layout == "legacy")


def test_explicit_kimi_home_overrides_detected_installation(configured, tmp_path):
    settings, _ = configured
    manager = ClientManager(
        settings,
        home=tmp_path / "home",
        environ={
            "KIMI_SHARE_DIR": str(tmp_path / "legacy"),
            "KIMI_CODE_HOME": str(tmp_path / "current"),
        },
    )
    assert manager.paths("kimi")[0] == tmp_path / "current" / "config.toml"
    assert manager.kimi_layout()[1] is False
    del manager.environ["KIMI_CODE_HOME"]
    assert manager.paths("kimi")[0] == tmp_path / "legacy" / "config.toml"
    assert manager.kimi_layout()[1] is True


def test_kimi_migration_preserves_selection_and_old_file(configured, tmp_path):
    settings, config = configured
    home = tmp_path / "home"
    legacy = ClientManager(settings, home=home, environ={"KIMI_SHARE_DIR": str(home / ".kimi")})
    catalog(config.parent, [capable(), capable("model-b"), model("not-selected")])
    legacy.apply(legacy.plan("kimi", selected_models=["model-a", "model-b"]))
    old_path = legacy.paths("kimi")[0]
    old_bytes = old_path.read_bytes()
    new_path = home / ".kimi-code" / "config.toml"
    new_path.parent.mkdir()
    new_path.write_text(
        'default_model="vela"\n'
        '[models.vela]\nprovider="vela"\nmodel="model-b"\nmax_context_size=1050000\n'
        '[providers.vela]\ntype="openai_responses"\nbase_url="http://old"\napi_key="old"\n'
        '[thinking]\nenabled=false\neffort="high"\n'
        '[providers.keep]\ntype="kimi"\napi_key="other-secret"\n',
        encoding="utf-8",
    )
    manager = ClientManager(settings, home=home, environ={})
    plan = manager.plan("kimi")
    assert plan.status == "Needs review"
    assert any("location changed" in line for line in plan.summary)
    assert plan.selected_models == ["model-a", "model-b"]
    assert manager.sync_configured() == ["Kimi: Needs review (open /clients)"]
    manager.apply(plan)
    doc = parse(new_path, new_path.read_bytes())
    assert set(doc["models"]) == {"vela/model-a", "vela/model-b"}
    assert doc["default_model"] == "vela/model-b"
    assert doc["thinking"] == {"enabled": False, "effort": "high"}
    assert doc["providers"]["keep"]["api_key"] == "other-secret"
    for value in doc["models"].values():
        assert value["support_efforts"] == ["low", "medium", "high", "xhigh", "max"]
        assert value["default_effort"] == "medium"
        assert set(value["capabilities"]) == {"image_in", "thinking", "always_thinking"}
        assert value["max_input_size"] == 272000
        assert value["max_output_size"] == 128000
    assert old_path.read_bytes() == old_bytes
    assert manager.plan("kimi").status == "Configured"


def test_kimi_code_chat_protocol_and_native_effort_fields(configured, tmp_path):
    settings, config = configured
    entry = capable()
    entry["supported_endpoints"] = ["/chat/completions"]
    entry["capabilities"]["supports"]["tool_calls"] = True
    entry["capabilities"]["supports"]["reasoning_effort"].insert(0, "none")
    entry["display_name"] = "Friendly Model"
    catalog(config.parent, [entry])
    manager = ClientManager(settings, home=tmp_path / "home", environ={})
    manager.apply(manager.plan("kimi"))
    path = manager.paths("kimi")[0]
    doc = parse(path, path.read_bytes())
    assert doc["providers"]["vela"]["type"] == "openai"
    info = doc["models"]["vela/model-a"]
    assert info["display_name"] == "Friendly Model"
    assert info["off_effort"] == "none"
    assert "none" not in info["support_efforts"]
    assert info["capabilities"] == ["image_in", "thinking", "tool_use"]


def test_kimi_code_delete_removes_dangling_default_not_other_settings(configured, tmp_path):
    settings, _ = configured
    manager = ClientManager(settings, home=tmp_path / "home", environ={})
    path = manager.paths("kimi")[0]
    path.parent.mkdir(parents=True)
    path.write_text('default_model="vela/model-a"\n[thinking]\nenabled=true\n', encoding="utf-8")
    manager.apply(manager.plan("kimi"))
    manager.apply(manager.plan("kimi", remove=True))
    doc = parse(path, path.read_bytes())
    assert "default_model" not in doc
    assert doc["thinking"]["enabled"] is True


@pytest.mark.skipif(not os.environ.get("VELA_TEST_KIMI_CODE"), reason="Kimi Code 2 not selected")
def test_installed_kimi_code_loads_multiple_models(configured, tmp_path):
    settings, config = configured
    entries = [capable("gpt-6-astra"), capable("gpt-5.6-luna")]
    entries[1]["capabilities"]["supports"]["reasoning_effort"].insert(0, "none")
    for entry in entries:
        entry["capabilities"]["supports"]["tool_calls"] = True
    catalog(config.parent, entries)
    manager = ClientManager(settings, home=tmp_path / "home", environ={})
    manager.apply(manager.plan("kimi"))
    env = os.environ.copy()
    env["KIMI_CODE_HOME"] = str(manager.paths("kimi")[0].parent)
    env["KIMI_DISABLE_TELEMETRY"] = "1"
    for name in list(env):
        if name.startswith("KIMI_MODEL_"):
            del env[name]
    executable = os.environ["VELA_TEST_KIMI_CODE"]
    doctor = subprocess.run(
        [executable, "doctor"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    listing = subprocess.run(
        [executable, "provider", "list", "--json"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert listing.returncode == 0, listing.stderr
    result = json.loads(listing.stdout)
    assert set(result["models"]) == {"vela/gpt-6-astra", "vela/gpt-5.6-luna"}
