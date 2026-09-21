from __future__ import annotations

import asyncio
import threading

import pytest
from test_model_context import catalog, model
from test_model_context import configured as configured
from textual.widgets import DataTable, OptionList, Static

from model_app import ContextScreen, CustomScreen, ModelApp, ModelsScreen
from model_store import write_context


@pytest.mark.parametrize("workspace", [False, True])
async def test_model_config_preview_is_read_only_and_reads_latest_file(configured, workspace):
    from model_config_screen import ModelConfigScreen
    from shell_app import ShellApp

    settings, path = configured
    app = (
        ShellApp(settings, refresh_on_start=False, initial_page="models")
        if workspace
        else ModelApp(settings, refresh_on_start=False)
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        before = path.read_bytes()
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ModelConfigScreen)
        assert "comment" in app.export_screenshot()
        assert app.screen.query_one(".subtitle", Static).render().plain == str(path)
        await pilot.press("a", "enter", "escape")
        assert app.screen is app.models_screen
        assert path.read_bytes() == before
        path.write_text(
            path.read_text(encoding="utf-8") + "\n# Updated preview\n", encoding="utf-8"
        )
        await pilot.press("p")
        await pilot.pause()
        assert "Updated" in app.export_screenshot()


async def test_model_config_preview_missing_file_does_not_create_it(configured):
    from model_config_screen import ModelConfigScreen

    settings, path = configured
    app = ModelApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        path.unlink()
        await pilot.press("p")
        await pilot.pause()
        assert isinstance(app.screen, ModelConfigScreen)
        assert "saved" in app.export_screenshot()
        assert not path.exists()


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_navigation_back_focus_and_custom_save(configured):
    settings, path = configured
    before = path.read_bytes()
    app = ModelApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await pilot.press("down", "enter")
        assert isinstance(app.screen, ContextScreen)
        assert app.screen.model_name == "model-b"
        await pilot.pause()
        assert "Follow" in app.export_screenshot()  # Options must render, not just accept keys.
        await pilot.press("down", "down", "enter", "down", "escape", "escape")
        assert isinstance(app.screen, ModelsScreen)
        assert app.models_screen.selected_model() == "model-b"
        assert path.read_bytes() == before
        await pilot.press("enter", "down", "down", "enter", "down", "enter")
        assert isinstance(app.screen, ModelsScreen)
        assert settings.context_preferences()["model-b"] == {"mode": "custom", "size": 1050000}
        assert settings.default_model == "model-a"
        await pilot.press("q")
        assert app.is_running
        await pilot.press("escape")
        assert not app.is_running


async def test_set_default_preserves_context_and_auto_removes_only_override(configured):
    settings, _ = configured
    app = ModelApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await pilot.press("enter", "down", "enter", "down", "d")
        assert settings.default_model == "model-b"
        assert settings.context_preferences()["model-a"] == {"mode": "maximum"}
        await pilot.press("up", "enter", "up", "enter")
        assert "model-a" not in settings.context_preferences()
        assert settings.default_model == "model-b"


async def test_refresh_defers_visible_options_and_revalidates_save(configured, monkeypatch):
    settings, path = configured
    app = ModelApp(settings, refresh_on_start=False)

    def refresh(_):
        catalog(path.parent, [model(maximum=400000), model("model-b")])

    monkeypatch.setattr("model_app.refresh_model_cache", refresh)
    async with app.run_test() as pilot:
        await pilot.press("enter", "down", "down", "enter")
        assert isinstance(app.screen, CustomScreen)
        await pilot.pause()
        assert "tokens" in app.export_screenshot()
        app.start_refresh()
        await wait_until(lambda: not app.refreshing)
        options = app.screen.query_one(OptionList)
        assert options.get_option_at_index(1).id == "1050000"
        await pilot.press("down", "enter")
        assert isinstance(app.screen, CustomScreen)
        assert "no longer available" in str(app.screen.query_one(".error", Static).render())
        assert not settings.context_preferences()
        await pilot.press("escape", "escape", "enter", "down", "down", "enter")
        assert app.screen.query_one(OptionList).get_option_at_index(1).id == "400000"


async def test_refresh_keeps_selected_model_when_catalog_reorders(configured, monkeypatch):
    settings, path = configured

    def refresh(_):
        catalog(path.parent, [model("model-b"), model()])

    monkeypatch.setattr("model_app.refresh_model_cache", refresh)
    app = ModelApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await pilot.press("down", "r")
        await wait_until(lambda: not app.refreshing)
        assert app.models_screen.selected_model() == "model-b"
        assert app.models_screen.query_one(DataTable).cursor_row == 0


async def test_slow_refresh_is_coalesced_and_exit_does_not_wait(configured, monkeypatch):
    settings, _ = configured
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_refresh(path):
        calls.append(path)
        started.set()
        release.wait(timeout=10)

    monkeypatch.setattr("model_app.refresh_model_cache", slow_refresh)
    app = ModelApp(settings)
    try:
        async with app.run_test() as pilot:
            await wait_until(started.is_set)
            await pilot.press("r", "r", "down", "escape")
            assert len(calls) == 1
            assert not app.is_running
        assert not release.is_set()
    finally:
        release.set()


async def test_failed_refresh_keeps_catalog_and_reports_failure(configured, monkeypatch):
    settings, _ = configured
    before = settings.model_cache_path.read_bytes()

    def fail(_):
        raise OSError("offline")

    monkeypatch.setattr("model_app.refresh_model_cache", fail)
    app = ModelApp(settings)
    async with app.run_test() as pilot:
        await wait_until(lambda: "Refresh failed" in app.notice)
        await pilot.pause()
        assert settings.model_cache_path.read_bytes() == before
        assert app.models_screen.query_one(DataTable).row_count == 2
        assert "offline" in str(app.models_screen.query_one(".status", Static).render())


async def test_unavailable_delete_is_explicit_and_does_not_change_default(configured):
    settings, path = configured
    write_context(path, "model-a", "custom", 272000)
    catalog(path.parent, [model("model-b")])
    app = ModelApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await pilot.click("#unavailable")
        await pilot.press("enter", "escape", "escape")
        assert settings.context_preferences()["model-a"]["size"] == 272000
        await pilot.click("#unavailable")
        await pilot.press("enter", "enter")
        assert isinstance(app.screen, ModelsScreen)
        assert "model-a" not in settings.context_preferences()
        assert settings.default_model == "model-a"


@pytest.mark.parametrize("size", [(48, 16), (80, 24), (120, 36)])
async def test_scroll_and_resize_keep_single_selected_row(configured, size):
    settings, path = configured
    catalog(path.parent, [model(f"model-{i:02}") for i in range(40)])
    app = ModelApp(settings, refresh_on_start=False)
    async with app.run_test(size=size) as pilot:
        table = app.models_screen.query_one(DataTable)
        await pilot.press(*(["down"] * 25))
        assert table.cursor_row == 25
        assert table.scroll_y > 0
        assert table.row_count == 40
        assert sum(column.width + 2 for column in table.ordered_columns) <= size[0] - 4
        assert "CONTEXT" in [str(column.label) for column in table.ordered_columns]
        await pilot.resize_terminal(100, 30)
        await pilot.press("enter", "escape")
        assert app.models_screen.selected_model() == "model-25"
        assert sum(column.width + 2 for column in table.ordered_columns) <= 96


async def test_unknown_options_and_failed_save_remain_editable(configured, monkeypatch):
    settings, path = configured
    catalog(path.parent, [{"name": "model-a", "upstream": "github_copilot/model-a"}])
    app = ModelApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await pilot.press("enter", "down", "enter")
        assert "Unknown" in str(app.screen.query_one(".error", Static).render())
        await pilot.press("down", "enter")
        assert app.screen.query_one(OptionList).option_count == 0
        await pilot.press("escape", "up", "up")

        def fail(*args):
            raise OSError("disk full")

        monkeypatch.setattr(app.manager, "save_context", fail)
        await pilot.press("enter")
        assert isinstance(app.screen, ContextScreen)
        assert "disk full" in str(app.screen.query_one(".error", Static).render())
