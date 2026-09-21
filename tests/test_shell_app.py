from __future__ import annotations

import asyncio
import sys
from io import StringIO

import pytest
from rich.console import Console
from test_model_context import configured as configured
from textual.widgets import Input, Static

import cli
import shell_app
from api_screen import ApiScreen
from model_app import ContextScreen, CustomScreen
from shell_app import AboutScreen, CommandBar, ShellApp, WorkspaceHeader


async def wait_done(app):
    async with asyncio.timeout(15):
        while app.busy:
            await asyncio.sleep(0.03)


async def test_frame_model_save_navigation_and_exit_confirmation(configured):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test(size=(120, 40)) as pilot:
        logo = app.wordmark.plain
        await pilot.press("/", "m", "tab", "enter")
        await pilot.pause()
        assert app.screen is app.models_screen
        assert len(app.screen.query(WorkspaceHeader)) == 1
        assert len(app.screen.query(CommandBar)) == 1
        await pilot.press("enter", "down", "enter")
        assert settings.context_preferences()["model-a"] == {"mode": "maximum"}
        await pilot.press("enter")
        assert isinstance(app.screen, ContextScreen)
        await pilot.press("escape")
        assert app.screen is app.models_screen
        assert not app.exit_pending
        await pilot.press("escape")
        assert app.screen is app.result_screen
        assert app.wordmark.plain == logo
        await pilot.press("escape")
        assert app.screen is app.result_screen
        assert app.is_running
        await pilot.press("escape")
        assert not app.is_running


async def test_exit_hint_preserves_input_and_expires_or_cancels(configured):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        entry = app.screen.query_one(Input)
        entry.value = "/api --"
        await pilot.pause()
        screen = app.screen
        await pilot.press("escape")
        assert app.screen is screen
        assert entry.has_focus
        assert entry.value == "/api --"
        hint = screen.query_one(".command-hint", Static)
        assert "Press Esc again" in str(hint.render())
        await pilot.resize_terminal(48, 18)
        assert "Press Esc again" in str(hint.render())
        await pilot.press("s")
        assert not app.exit_pending
        assert entry.value == "/api --s"
        assert "Press Esc again" not in str(hint.render())
        app.EXIT_CONFIRM_SECONDS = 0.4
        await pilot.press("escape")
        await pilot.pause(0.5)
        assert not app.exit_pending
        assert "Press Esc again" not in str(hint.render())
        await pilot.press("escape")
        assert app.is_running  # An expired first press cannot confirm exit.
        await pilot.click("#command")
        assert not app.exit_pending


async def test_slash_focus_and_contextual_hints_without_changing_input_slashes(configured):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:

        def hint():
            return str(app.screen.query_one(".command-hint", Static).render())

        assert hint() == "Enter Run · Esc Exit"
        await app.submit_command("/models")
        await pilot.pause()
        assert hint() == "↑↓ Select · Enter Edit · D Default · R Refresh · P Config · Esc Back"
        await pilot.press("/")
        entry = app.screen.query_one(Input)
        assert entry.has_focus and entry.value == "/"
        assert hint() == "↑↓ Select · Tab Complete · Enter Run · Esc Back"
        await pilot.press("a", "p", "i", "space", "/")
        assert entry.value == "/api /"  # Already focused: slash is ordinary text.
        assert hint() == "Enter Run · Esc Back"
        await pilot.press("escape")
        assert app.is_home and not app.exit_pending
        await app.submit_command("/models")
        await pilot.pause()
        await pilot.press("enter", "down", "down", "enter")
        assert isinstance(app.screen, CustomScreen)
        await pilot.press("/")
        assert app.screen.query_one(Input).value == "/"
        assert app.screen.query_one(Input).has_focus
        await pilot.press("escape")
        assert isinstance(app.screen, ContextScreen) and not app.exit_pending
        await pilot.press("escape")
        assert app.screen is app.models_screen and not app.exit_pending
        await pilot.press("escape")
        assert app.is_home and not app.exit_pending
        assert hint() == "Enter Run · Esc Exit"


async def test_command_results_return_home_before_exit_can_be_armed(configured):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await app.submit_command("/test --help")
        await wait_done(app)
        assert not app.is_home
        assert "Esc Back" in str(app.screen.query_one(".command-hint", Static).render())
        await pilot.press("escape")
        assert app.is_home and not app.exit_pending
        await pilot.press("escape")
        assert app.exit_pending and app.is_running
        await pilot.press("escape")
        assert not app.is_running


async def test_about_completion_content_and_back(configured, monkeypatch):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)

    def unexpected_command(*args):
        pytest.fail("About must not launch an external command")

    monkeypatch.setattr(app, "run_command", unexpected_command)
    async with app.run_test() as pilot:
        await pilot.press("/", "a", "b", "tab")
        assert app.screen.query_one(Input).value == "/about "
        await pilot.press("enter")
        assert isinstance(app.screen, AboutScreen)
        output = StringIO()
        Console(file=output, width=100).print(
            app.screen.query_one("#about-content", Static).renderable
        )
        rendered = output.getvalue()
        assert app.version in rendered
        assert "GitHub Copilot" in rendered and "Anthropic" in rendered
        assert "https://github.com/LittleGraper/vela-llm" in rendered
        assert settings.local_api_key not in rendered
        await pilot.press("escape")
        assert app.is_home and not app.exit_pending


async def test_ctrl_c_uses_inline_esc_confirmation(configured):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+c")
        assert app.exit_pending
        assert app.screen is app.result_screen
        await pilot.press("escape")
        assert not app.is_running


@pytest.mark.parametrize("leave", ["escape", "/models"])
async def test_api_key_toggle_is_local_and_resets_when_leaving(configured, leave):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.submit_command("/api")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ApiScreen)
        key = screen.query_one("#api-key", Static)
        assert key.render().plain == cli.mask_api_key(settings.local_api_key)
        assert "K Show key" in screen.query_one(".command-hint", Static).render().plain
        await pilot.press("k")
        assert key.render().plain == settings.local_api_key
        assert "K Hide key" in screen.query_one(".command-hint", Static).render().plain
        await pilot.click("#toggle-key")
        assert key.render().plain == cli.mask_api_key(settings.local_api_key)
        await pilot.press("/")
        await pilot.press("k")
        assert screen.query_one(Input).value == "/k"
        assert not screen.key_visible  # Typing commands must not reveal the key.
        await pilot.pause(0.3)  # Let the previous button press animation finish.
        await pilot.click("#toggle-key")
        assert screen.key_visible
        if leave == "escape":
            await pilot.press("escape")
        else:
            await app.submit_command(leave)
            await pilot.pause()
            assert not screen.key_visible
            await pilot.press("escape")
        assert app.is_home and not app.exit_pending
        assert not screen.key_visible
        assert settings.local_api_key not in app.result_text
        await app.submit_command("/api")
        await pilot.pause()
        assert not app.screen.key_visible
        assert app.screen.query_one("#api-key", Static).render().plain != settings.local_api_key


async def test_api_explicit_reveal_preserves_long_literal_key(configured):
    settings, _ = configured
    settings.local_api_key = "sk-[red]" + "x" * 150 + "[/red]"
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test(size=(48, 24)) as pilot:
        await app.submit_command("/api --show-key")
        await pilot.pause()
        key = app.screen.query_one("#api-key", Static)
        assert key.render().plain == settings.local_api_key
        assert key.region.height > 1
        assert key.region.right <= 48
        await pilot.press("enter")
        assert not app.screen.key_visible


async def test_api_missing_key_disables_reveal(configured):
    settings, _ = configured
    settings.local_api_key = ""
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await app.submit_command("/api")
        await pilot.pause()
        assert app.screen.query_one("#api-key", Static).render().plain == "Not configured"
        assert app.screen.query_one("#toggle-key").disabled
        await pilot.press("k")
        assert not app.screen.key_visible


async def test_command_completion_and_live_results_replace_previous_output(configured):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await pilot.press("/", "s", "down", "tab")
        assert app.screen.query_one(Input).value == "/stop "
        app.screen.query_one(Input).value = ""
        await app.submit_command("/api --help")
        await wait_done(app)
        assert "--show-key" in app.result_text
        assert settings.local_api_key not in app.result_text
        rendered = app.screen.query_one("#result", Static).render()
        assert "\x1b" not in rendered.plain
        colors = {
            span.style.foreground.hex.lower() for span in rendered.spans if span.style.foreground
        }
        assert {"#70dfdf", "#95aab9", "#304754"} <= colors
        await app.submit_command("/test --help")
        await wait_done(app)
        assert "--show-key" not in app.result_text
        assert "--timeout" in app.result_text
        assert app.screen.query_one(Input).has_focus
        await app.submit_command("/test --timeout invalid")
        await wait_done(app)
        assert "Failed (2)" in str(app.screen.query_one("#result-title", Static).render())
        assert "usage:" not in app.result_text.lower()


async def test_split_color_sequence_does_not_leak_escape_text(configured):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test():
        app.showing_directory = False
        app.set_result("/login · Running…", "Device code: \x1b[38;2;112;")
        assert app.screen.query_one("#result", Static).render().plain == "Device code: "
        app.set_result("/login · Running…", "Device code: \x1b[38;2;112;223;223mABCD\x1b[0m")
        rendered = app.screen.query_one("#result", Static).render()
        assert rendered.plain == "Device code: ABCD"
        assert any(span.style.foreground.hex.lower() == "#70dfdf" for span in rendered.spans)


async def test_model_test_subprocess_displays_only_latest_table(configured, monkeypatch):
    settings, _ = configured
    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        return await real_spawn(
            sys.executable,
            "-u",
            "-c",
            "from cli import ModelTestProgress, ModelTestResult; "
            "from cli_ui import message; "
            "entry = {'name': 'model-a'}; "
            "message('Testing configured models...'); "
            "progress = ModelTestProgress([entry]); progress.start(); "
            "progress.finish(ModelTestResult(entry, 123)); progress.stop(); "
            "message('All configured models are reachable.', level='success')",
            **kwargs,
        )

    monkeypatch.setattr(shell_app.asyncio, "create_subprocess_exec", spawn)
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test():
        await app.submit_command("/test")
        await wait_done(app)
        rendered = app.screen.query_one("#result", Static).render()
        assert rendered.plain.count("STATUS") == 1
        assert "1/1 complete" in rendered.plain and "0/1 complete" not in rendered.plain
        assert "PASS" in rendered.plain and "RUN" not in rendered.plain
        assert "All configured models are reachable" in rendered.plain
        assert "\x1e" not in rendered.plain and "vela_model_test" not in rendered.plain
        assert "Done" in app.screen.query_one("#result-title", Static).render().plain


async def test_subprocess_stream_exit_cancels_and_no_concurrent_command(configured, monkeypatch):
    settings, _ = configured
    real_spawn = asyncio.create_subprocess_exec
    calls = []
    processes = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        process = await real_spawn(
            sys.executable,
            "-u",
            "-c",
            "import time; print('DEVICE-CODE-1234', flush=True); time.sleep(60)",
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(shell_app.asyncio, "create_subprocess_exec", spawn)
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test() as pilot:
        await app.submit_command("/login")
        async with asyncio.timeout(10):
            while "DEVICE-CODE" not in app.result_text:
                await asyncio.sleep(0.03)
        assert app.busy  # Device code is displayed while authentication is still waiting.
        await app.submit_command("/stop")
        assert len(calls) == 1
        await pilot.press("escape", "/")
        assert not app.exit_pending
        assert app.busy
        await app.submit_command("/models")
        await pilot.pause()
        await pilot.press("escape")
        assert app.is_home and not app.exit_pending
        await pilot.press("escape", "escape")
        assert not app.is_running
    assert processes[0].returncode is not None


@pytest.mark.parametrize("size", [(48, 18), (80, 24), (120, 40)])
async def test_header_and_input_stay_visible_on_all_pages_and_resize(configured, size):
    settings, _ = configured
    app = ShellApp(settings, refresh_on_start=False)
    async with app.run_test(size=size) as pilot:
        for command in ("/models", "/api", "/about", None, "/models"):
            if command is None:
                await pilot.press("escape")
            else:
                await app.submit_command(command)
            await pilot.pause()
            header = app.screen.query_one(WorkspaceHeader)
            entry = app.screen.query_one(Input)
            body = app.screen.query_one(".workspace-body")
            assert header.region.bottom <= body.region.y
            assert body.region.bottom <= app.screen.query_one(CommandBar).region.y
            assert entry.region.bottom <= size[1]
            assert entry.region.width <= size[0]
            assert body.region.height >= 3
        await pilot.resize_terminal(100, 36)
        await pilot.pause()
        assert app.screen.query_one(WorkspaceHeader).region.height == 13
        assert (
            app.wordmark.plain.strip() in app.screen.query_one("#wordmark", Static).render().plain
        )


def test_interactive_no_args_opens_workspace_but_help_does_not(configured, monkeypatch, capsys):
    settings, _ = configured
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    opened = []
    monkeypatch.setattr(ShellApp, "run", lambda app: opened.append(app))
    cli.main([])
    assert len(opened) == 1
    cli.main(["help"])
    assert len(opened) == 1
    assert "Commands" in capsys.readouterr().out
