from __future__ import annotations

import asyncio
import threading

import pytest
from test_model_context import catalog, model
from test_model_context import configured as configured
from textual.widgets import DataTable, Input, OptionList, Static

import shell_app
from client_config import CLIENTS, ClientManager
from client_health import ClientCheck
from client_screens import (
    ClientConfigScreen,
    ClientModelsScreen,
    ClientPreviewScreen,
    ClientsScreen,
    ModelChecklist,
)
from shell_app import COMMAND_GROUPS, CommandBar, ShellApp, WorkspaceHeader


@pytest.fixture
def client_app(configured, tmp_path, monkeypatch):
    settings, _ = configured
    manager = ClientManager(settings, home=tmp_path / "home", environ={})
    monkeypatch.setattr(shell_app, "ClientManager", lambda _, **kwargs: manager)
    monkeypatch.setattr(
        "client_config.refresh_model_cache",
        lambda path: manager.settings.raw_model_registry(),
    )
    monkeypatch.setattr(
        "client_screens.check_client",
        lambda *args, **kwargs: ClientCheck(True, "Proxy reachable; inference not tested."),
    )
    return ShellApp(settings, refresh_on_start=False), manager


def hint(app):
    return str(app.screen.query_one(".command-hint", Static).render())


def test_kimi_activation_hint_uses_version_independent_restart(client_app):
    _, manager = client_app
    assert ClientConfigScreen(manager, "kimi").activation_hint() == (
        "Restart Kimi, then use /model."
    )


def rendered_colors(app, selector, text=None):
    widget = app.screen.query_one(selector, Static)
    return {
        segment.style.color.get_truecolor().hex
        for strip in widget.render_lines(widget.region.size.region)
        for segment in strip
        if segment.text.strip()
        and (text is None or text in segment.text)
        and segment.style
        and segment.style.color
    }


async def test_dsh_versioned_credentials_allow_apply_after_model_change(client_app):
    app, manager = client_app
    path = manager.paths("dsh")[1]
    path.parent.mkdir(parents=True)
    path.write_text(
        "version: 1\nrefs: {}\nrecords:\n  llm-pi-ai/openai-codex:\n"
        "    kind: grant\n    payload:\n      access: isolated-test-secret\n"
    )
    async with app.run_test() as pilot:
        await app.submit_command("/clients")
        await pilot.pause()
        await pilot.press("enter", "up", "enter", "space", "enter")
        assert app.screen.plan.selected_models == ["model-b"]
        assert not app.screen.plan.error
        assert not app.screen.query_one(OptionList).get_option("apply").disabled
        await pilot.press("enter")
        async with asyncio.timeout(10):
            while not app.screen.saved:
                await pilot.pause()
        assert app.screen.plan.status == "Configured"
        assert "isolated-test-secret" in path.read_text()


async def test_models_refresh_automatically_syncs_clients(client_app, monkeypatch):
    import client_config

    app, manager = client_app
    manager.apply(manager.plan("kimi"))
    monkeypatch.setattr(client_config, "ClientManager", lambda _: manager)

    def refresh(path):
        catalog(path.parent, [model(), model("new-model")])

    monkeypatch.setattr("model_app.refresh_model_cache", refresh)
    async with app.run_test() as pilot:
        await app.submit_command("/models")
        await pilot.pause()
        await pilot.press("r")
        async with asyncio.timeout(10):
            while app.refreshing:
                await pilot.pause()
        assert "Kimi: models synced" in app.notice
        assert manager.plan("kimi").status == "Configured"
        text = manager.paths("kimi")[0].read_text()
        assert "new-model" in text and "model-b" not in text


async def test_configure_refresh_failure_is_not_success(client_app, monkeypatch):
    app, manager = client_app

    def fail(path):
        raise ValueError("Upstream catalog unavailable")

    monkeypatch.setattr("client_config.refresh_model_cache", fail)
    async with app.run_test() as pilot:
        await app.submit_command("/clients")
        await pilot.pause()
        await pilot.press("down", "enter", "enter")
        async with asyncio.timeout(10):
            while app.screen.saving:
                await pilot.pause()
        assert not app.screen.saved
        assert "refresh failed" in app.screen.message
        assert not any(path.exists() for path in manager.paths("codex"))
        assert app.screen.connection is None
        assert rendered_colors(app, "#client-message") == {"#f2c677"}


async def test_configured_offline_status_is_distinct(client_app, monkeypatch):
    app, manager = client_app
    monkeypatch.setattr(
        "client_screens.check_client",
        lambda *args, **kwargs: ClientCheck(False, "Proxy unreachable. Run /start."),
    )
    async with app.run_test() as pilot:
        await app.submit_command("/clients")
        await pilot.pause()
        await pilot.press("down", "enter", "enter")
        async with asyncio.timeout(10):
            while not app.screen.saved:
                await pilot.pause()
        assert app.screen.plan.status == "Configured"
        assert not app.screen.connection.ok
        assert "/start" in app.screen.connection.message
        assert not app.screen.query_one(OptionList).get_option("test").disabled


async def test_request_feedback_colors_follow_result_not_saved_flag(client_app, monkeypatch):
    app, manager = client_app
    manager.apply(manager.plan("kimi"))
    async with app.run_test(size=(120, 40)) as pilot:
        await app.submit_command("/clients")
        await pilot.pause()
        await pilot.press("down", "down", "enter")
        screen = app.screen
        assert rendered_colors(app, "#client-details", "Not checked") == {"#95aab9"}
        for ok in (True, False, True):
            started = threading.Event()
            release = threading.Event()
            message = "Text request passed for model-a." if ok else "Proxy unreachable. Run /start."
            result = ClientCheck(ok, message)

            def check(*args, started=started, release=release, result=result, **kwargs):
                started.set()
                assert release.wait(5), "Test did not release the connection check"
                return result

            monkeypatch.setattr("client_screens.check_client", check)
            worker = screen.test_connection()
            try:
                async with asyncio.timeout(5):
                    while not started.is_set():
                        await pilot.pause()
                await pilot.pause()
                assert rendered_colors(app, "#client-message") == {"#95aab9"}
                assert screen.connection is None
            finally:
                release.set()
                await worker.wait()
            await pilot.pause()
            assert not screen.saved
            assert screen.connection.ok is ok
            assert screen.plan.status == "Configured"
            assert rendered_colors(app, "#client-message") == {"#9ad68b" if ok else "#f2c677"}


async def test_kimi_picker_includes_responses_models_and_previews_protocol(client_app):
    app, manager = client_app
    catalog(
        manager.settings.model_cache_path.parent,
        [
            {**model(), "supported_endpoints": ["/chat/completions"]},
            {**model("gpt-6-astra"), "supported_endpoints": ["/responses"]},
        ],
    )
    async with app.run_test() as pilot:
        await app.submit_command("/clients")
        await pilot.pause()
        await pilot.press("down", "down", "enter", "up", "enter")
        assert isinstance(app.screen, ClientModelsScreen)
        options = app.screen.query_one(OptionList)
        assert options.option_count == 2
        assert options.get_option_at_index(0).value == "gpt-6-astra"
        await pilot.press("down", "space", "enter", "p")
        assert isinstance(app.screen, ClientPreviewScreen)
        assert app.screen.plan.selected_models == ["gpt-6-astra"]
        assert "openai_responses" in app.screen.plan.changes[0].preview()[0]
        assert not manager.paths("kimi")[0].exists()


async def test_full_client_flow_preview_model_save_and_navigation(client_app):
    app, manager = client_app
    assert ("Models", ("models", "test", "clients")) in COMMAND_GROUPS
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("/", "c", "tab", "enter")
        assert isinstance(app.screen, ClientsScreen)
        table = app.screen.query_one(DataTable)
        assert table.row_count == 3
        assert [str(table.get_row_at(index)[0]) for index in range(3)] == ["DSH", "Codex", "Kimi"]
        assert hint(app) == "↑↓ Select · Enter Configure · Esc Back"
        await pilot.press("r")
        assert isinstance(app.screen, ClientsScreen)
        await pilot.press("enter")
        assert isinstance(app.screen, ClientConfigScreen)
        assert app.screen.query_one(OptionList).highlighted == 1
        assert hint(app) == "↑↓ Select · Enter Select · P Preview · Esc Back"
        await pilot.press("p")
        assert isinstance(app.screen, ClientPreviewScreen)
        assert hint(app) == "↑↓ Scroll · Esc Back"
        assert not manager.paths("dsh")[0].exists()
        await pilot.press("escape", "up", "enter")
        assert isinstance(app.screen, ClientModelsScreen)
        assert set(app.screen.query_one(ModelChecklist).selected) == {"model-a", "model-b"}
        await pilot.press("space", "enter")
        assert app.screen.plan.selected_models == ["model-b"]
        assert not manager.paths("dsh")[0].exists()
        await pilot.press("enter")
        async with asyncio.timeout(10):
            while not app.screen.saved:
                await pilot.pause()
        assert app.screen.plan.status == "Configured"
        assert manager.plan("dsh").selected_models == ["model-b"]
        await pilot.press("escape")
        assert isinstance(app.screen, ClientsScreen)
        assert str(app.screen.query_one(DataTable).get_row_at(0)[1]) == "Configured"
        await pilot.press("escape")
        assert app.is_home and not app.exit_pending


@pytest.mark.parametrize("size", [(48, 18), (80, 24), (120, 40)])
async def test_frame_input_preview_and_draft_discard(client_app, size):
    app, manager = client_app
    async with app.run_test(size=size) as pilot:
        await app.submit_command("/clients")
        await pilot.pause()
        await pilot.press("down", "enter")
        screen = app.screen
        assert len(screen.query(WorkspaceHeader)) == len(screen.query(CommandBar)) == 1
        await pilot.press("/", "p")
        assert app.screen is screen
        assert screen.query_one(Input).value == "/p"
        entry = screen.query_one(Input)
        assert entry.region.bottom <= size[1]
        await pilot.press("escape", "enter", "up", "enter", "space", "enter")
        assert app.screen.plan.selected_models == ["model-b"]
        await pilot.press("escape", "enter")
        assert app.screen.plan.selected_models is None
        assert not any(p.exists() for p in manager.paths("codex"))


async def test_manual_modification_requires_preview_and_parse_error_blocks(client_app):
    app, manager = client_app
    manager.apply(manager.plan("dsh"))
    path = manager.paths("dsh")[0]
    path.write_bytes(path.read_bytes().replace(b"model-a", b"manual-model"))
    async with app.run_test() as pilot:
        await app.submit_command("/clients")
        await pilot.pause()
        await pilot.press("enter", "p")
        assert isinstance(app.screen, ClientPreviewScreen)
        assert b"manual-model" in path.read_bytes()
        await pilot.press("escape", "escape")
        path.write_text("bad: [unclosed")
        await pilot.press("enter")
        assert app.screen.plan.error
        assert app.screen.query_one(OptionList).get_option("apply").disabled


@pytest.mark.parametrize("size", [(48, 18), (120, 40)])
@pytest.mark.parametrize("client", CLIENTS)
async def test_checklist_discard_empty_configure_and_delete(client_app, size, client):
    app, manager = client_app
    async with app.run_test(size=size) as pilot:
        await app.submit_command("/clients")
        await pilot.pause()
        await pilot.press(*(["down"] * list(CLIENTS).index(client)), "enter")
        assert app.screen.query_one(OptionList).get_option("remove").disabled
        assert "Default model" not in str(
            app.screen.query_one(OptionList).get_option_at_index(0).prompt
        )
        await pilot.press("up", "enter", "space", "escape")
        assert app.screen.plan.selected_models is None
        await pilot.press("enter", "space", "down", "space", "enter")
        assert app.screen.plan.selected_models == []
        assert app.screen.query_one(OptionList).get_option("apply").disabled
        await pilot.press("enter", "a", "enter")
        assert app.screen.plan.selected_models is None
        await pilot.press("enter")
        async with asyncio.timeout(10):
            while not app.screen.saved:
                await pilot.pause()
        assert not app.screen.query_one(OptionList).get_option("remove").disabled
        assert len(app.screen.query(WorkspaceHeader)) == len(app.screen.query(CommandBar)) == 1
        assert app.screen.query_one(Input).region.bottom <= size[1]
        await pilot.press("down", "enter")
        async with asyncio.timeout(10):
            while (manager.state_dir / f"{client}.json").exists():
                await pilot.pause()
        await pilot.pause()
        assert app.screen.plan.status == "Not configured"
        assert app.screen.query_one(OptionList).get_option("remove").disabled
        assert manager.sync_configured() == []
