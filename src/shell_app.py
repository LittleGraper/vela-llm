"""Persistent terminal workspace: one frame, one command, one current result."""

from __future__ import annotations

import asyncio
import codecs
import os
import re
import shlex
import subprocess
import sys
from contextlib import suppress
from time import monotonic

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Event, Key, Mount, MouseDown, Paste
from textual.timer import Timer
from textual.widgets import DataTable, Input, OptionList, Static

from api_screen import ApiScreen
from banner import render_startup_banner
from model_app import ModelApp
from process_lifetime import WORKSPACE_JOB_ENV, WorkspaceJobs, command_python
from workspace_output import CommandOutput
from workspace_screen import WorkspaceScreen

COMMANDS = {
    "models": "Manage models and context",
    "start": "Start the proxy in the background",
    "stop": "Stop the proxy",
    "api": "API addresses and masked key",
    "test": "Test model connectivity",
    "login": "Sign in with GitHub",
    "whoami": "Current GitHub account",
    "logout": "Remove saved credentials",
    "update": "Update to latest release",
    "about": "About VELA",
}

COMMAND_GROUPS = (
    ("Proxy", ("start", "stop", "api")),
    ("Models", ("models", "test")),
    ("Account", ("login", "whoami", "logout")),
    ("VELA", ("update", "about")),
)


class CommandDirectory:
    """Lay out the complete directory using the current result pane width."""

    def __rich_console__(self, console, options):
        sections = []
        for title, names in COMMAND_GROUPS:
            table = Table.grid(padding=(0, 2), expand=True)
            table.add_column(style="bold #70dfdf", width=9, no_wrap=True)
            table.add_column(style="#95aab9", ratio=1)
            for name in names:
                table.add_row("/" + name, COMMANDS[name])
            sections.append(Group(Text(title, style="bold #d9e4ec"), table, Text("")))
        if options.max_width >= 104:
            layout = Table.grid(padding=(0, 4), expand=True)
            layout.add_column(ratio=1)
            layout.add_column(ratio=1)
            for index in range(0, len(sections), 2):
                layout.add_row(
                    sections[index], sections[index + 1] if index + 1 < len(sections) else ""
                )
            yield layout
        else:
            yield from sections


class WorkspaceHeader(Horizontal):
    def compose(self) -> ComposeResult:
        with Vertical(id="workspace-brand"):
            yield Static(id="wordmark", markup=False)
        with Vertical(id="workspace-details"):
            yield Static(id="workspace-info", markup=False)

    def on_mount(self) -> None:
        self.update_header()
        self.set_interval(3, self.update_header)

    def on_resize(self) -> None:
        self.update_header()

    def update_header(self) -> None:
        from cli import is_process_running, read_pid_record
        from config_files import active_config_dir

        if not self.is_mounted:
            return
        app = self.app
        settings = app.manager.settings
        compact = app.size.height < 34 or app.size.width < 96
        self.set_class(compact, "compact")
        self.query_one("#workspace-brand").styles.min_width = (
            7 if compact else max(map(len, app.wordmark.plain.splitlines()))
        )
        self.query_one("#wordmark", Static).update(
            Text("VELA", style="bold #70dfdf") if compact else app.wordmark
        )
        record = read_pid_record(active_config_dir() / ".vela-llm.pid")
        running = record is not None and is_process_running(record.pid)
        state = "Running" if running else "Stopped"
        info = Text(no_wrap=True, overflow="ellipsis")
        state_style = "#9ad68b" if running else "#95aab9"
        if compact:
            info.append(f"v{app.version}", style="#95aab9")
            info.append(f" · {state}\n", style=state_style)
            info.append(f"Model  {settings.default_model}", style="#d9e4ec")
        else:
            info.append("LOCAL API", style="bold #d9e4ec")
            info.append(f"  /  v{app.version}\n\n", style="#95aab9")
            info.append("Proxy  ", style="#95aab9")
            info.append(f"● {state}\n\n", style=state_style)
            info.append("Default model\n", style="#95aab9")
            info.append(settings.default_model, style="bold #70dfdf")
        self.query_one("#workspace-info", Static).update(info)


class CommandInput(Input):
    BINDINGS = [
        Binding("up", "suggest(-1)", show=False),
        Binding("down", "suggest(1)", show=False),
        Binding("tab", "complete", show=False),
    ]

    def action_suggest(self, direction: int) -> None:
        self.parent.select_suggestion(direction)

    def action_complete(self) -> None:
        bar = self.parent
        if bar.matches:
            self.value = "/" + bar.matches[bar.selected] + " "
            self.cursor_position = len(self.value)
        else:
            self.screen.focus_next()


class CommandBar(Vertical):
    def __init__(self) -> None:
        super().__init__()
        self.matches: list[str] = []
        self.selected = 0

    def compose(self) -> ComposeResult:
        yield Static(id="suggestions", markup=False)
        yield CommandInput(id="command", select_on_focus=False)
        yield Static("", classes="command-hint")

    def on_mount(self) -> None:
        self.render_suggestions()

    def on_resize(self) -> None:
        self.update_hint()

    def update_hint(self) -> None:
        if self.app.exit_pending:
            hint = "Press Esc again to exit"
            if self.app.busy:
                hint += "\nRunning command will be cancelled."
            self.query_one(".command-hint", Static).update(Text(hint, style="bold #f2c677"))
            return
        focused = self.screen.focused
        if isinstance(focused, Input):
            hint = "↑↓ Select · Tab Complete · Enter Run" if self.matches else "Enter Run"
        elif isinstance(self.screen, ApiScreen):
            hint = "K Hide key" if self.screen.key_visible else "K Show key"
            if isinstance(focused, VerticalScroll):
                hint = "↑↓ Scroll · " + hint
            else:
                hint = "Enter Toggle · " + hint
        elif isinstance(focused, (DataTable, OptionList)):
            hint = "↑↓ Select · Enter Edit"
            if isinstance(focused, DataTable) and self.screen is self.app.models_screen:
                hint += (
                    " · D Default · R Refresh · P Config"
                    if self.app.size.width < 96
                    else " · D Set default · R Refresh · P Preview config"
                )
        elif isinstance(focused, VerticalScroll):
            hint = "↑↓ Scroll"
        else:
            hint = "Enter Select"
        hint += " · Esc Exit" if self.app.is_home else " · Esc Back"
        self.query_one(".command-hint", Static).update(hint)

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value.lstrip("/")
        self.matches = (
            [name for name in COMMANDS if name.startswith(value)]
            if event.value and not any(c.isspace() for c in event.value)
            else []
        )
        self.selected = 0
        self.render_suggestions()

    def select_suggestion(self, direction: int) -> None:
        if self.matches:
            self.selected = (self.selected + direction) % len(self.matches)
            self.render_suggestions()

    def render_suggestions(self) -> None:
        lines = Text()
        start = max(0, self.selected - 2)
        for index in range(start, min(len(self.matches), start + 3)):
            name = self.matches[index]
            lines.append(
                f"{'›' if index == self.selected else ' '} /{name:<10} {COMMANDS[name]}\n",
                style="bold #70dfdf" if index == self.selected else "#95aab9",
            )
        widget = self.query_one("#suggestions", Static)
        lines.rstrip()
        widget.update(lines)
        widget.display = bool(self.matches) and self.query_one(Input).has_focus
        self.update_hint()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value.strip()
        if self.matches:
            value = "/" + self.matches[self.selected]
        if value:
            event.input.value = ""
            await self.app.submit_command(value)


class ResultScreen(WorkspaceScreen):
    def compose_content(self) -> ComposeResult:
        yield Static("Workspace", id="result-title", classes="heading", markup=False)
        with VerticalScroll(id="result-scroll"):
            yield Static(id="result", markup=False)

    def on_mount(self) -> None:
        self.app.show_home()
        self.query_one(Input).focus()


class AboutScreen(WorkspaceScreen):
    AUTO_FOCUS = "#about-scroll"
    DEFAULT_CSS = """
    AboutScreen #about-content {
        height: auto; max-width: 88; border: round #304754; padding: 1 2;
    }
    """

    def compose_content(self) -> ComposeResult:
        details = Table.grid(padding=(0, 2))
        details.add_column(style="#95aab9")
        details.add_column(style="#d9e4ec", overflow="fold")
        details.add_row("Version", self.app.version)
        details.add_row("GitHub", "https://github.com/LittleGraper/vela-llm")
        details.add_row("PyPI", "https://pypi.org/project/vela-llm/")
        yield Static("VELA  /  About", classes="heading")
        with VerticalScroll(id="about-scroll"):
            yield Static(
                Group(
                    Text("A local API proxy for GitHub Copilot models.", style="bold #70dfdf"),
                    Text(
                        "Compatible with OpenAI and Anthropic APIs, powered by LiteLLM.",
                        style="#95aab9",
                    ),
                    Text(""),
                    details,
                ),
                id="about-content",
            )


class ShellApp(ModelApp, inherit_bindings=False):
    TITLE = "VELA"
    EXIT_CONFIRM_SECONDS = 3.0
    persistent_workspace = True
    BINDINGS = [
        Binding("ctrl+c", "arm_exit", show=False, priority=True),
        Binding("escape", "request_exit", "Exit", priority=True),
        Binding("tab", "complete_or_focus", show=False, priority=True),
    ]
    CSS = (
        ModelApp.CSS
        + """
    WorkspaceScreen { padding: 1 2 0 2; }
    WorkspaceHeader {
        dock: top; height: 13; border: round #304754; padding: 0 1; margin-bottom: 1;
    }
    #workspace-brand { width: 55%; height: 11; align: center middle; margin-right: 4; }
    #wordmark { width: auto; height: 11; color: #70dfdf; }
    #workspace-details { width: 1fr; height: 1fr; align: left middle; }
    #workspace-info { width: 1fr; height: auto; }
    WorkspaceHeader.compact { height: 5; }
    WorkspaceHeader.compact #workspace-brand { width: 7; height: 3; margin-right: 2; }
    WorkspaceHeader.compact #wordmark { width: auto; height: 1; }
    CommandBar { dock: bottom; height: auto; padding-top: 1; background: #101820; }
    #suggestions { height: auto; max-height: 3; padding: 0 1; }
    CommandInput { border: round #304754; background: #15222d; }
    CommandInput:focus { border: round #70dfdf; }
    .command-hint { height: auto; max-height: 2; color: #95aab9; }
    .workspace-body { height: 1fr; }
    .workspace-body Footer { display: none; }
    .workspace-body .heading { margin-bottom: 0; }
    .workspace-body .subtitle { margin-bottom: 0; }
    #result-scroll { height: 1fr; }
    #result { height: auto; padding: 1; }
    """
    )

    def __init__(
        self, settings, *, refresh_on_start: bool = True, initial_page: str = "home"
    ) -> None:
        from cli import package_version

        super().__init__(settings, refresh_on_start=refresh_on_start)
        self.wordmark = Text.from_ansi(render_startup_banner(color="NO_COLOR" not in os.environ))
        self.version = package_version()
        self.result_screen = ResultScreen()
        self.busy = False
        self.process: asyncio.subprocess.Process | None = None
        self.result_text = ""
        self.initial_page = initial_page
        self.showing_directory = True
        self.exit_deadline = 0.0
        self.exit_timer: Timer | None = None
        self.process_job: WorkspaceJobs | None = None

    def on_mount(self, event: Mount) -> None:
        event.prevent_default()
        # This screen is reused after navigating through other workspace pages.
        self.install_screen(self.models_screen, "models")
        self.push_screen(self.result_screen)
        if self.initial_page == "models":
            self.call_after_refresh(self.submit_command, "/models")

    def action_back(self) -> None:
        # Existing model pages route Esc here; subpages go back without arming exit.
        self.action_request_exit()

    def on_unmount(self) -> None:
        if self.process_job is not None:
            self.process_job.close()

    @property
    def is_home(self) -> bool:
        return self.screen is self.result_screen and self.showing_directory

    @property
    def exit_pending(self) -> bool:
        return monotonic() < self.exit_deadline

    async def on_event(self, event: Event) -> None:
        # Inspect keys before focused inputs/tables consume them or bindings run.
        if (
            isinstance(event, Key) and not event.is_forwarded and event.key != "escape"
        ) or isinstance(event, (MouseDown, Paste)):
            self.cancel_exit()
        if (
            isinstance(event, Key)
            and not event.is_forwarded
            and event.character == "/"
            and not isinstance(self.focused, Input)
        ):
            entry = self.screen.query_one(Input)
            if not entry.value.startswith("/"):
                entry.value = "/" + entry.value
            self.screen.set_focus(entry)
            entry.cursor_position = len(entry.value)
            event.stop()
            return
        await super().on_event(event)

    def on_descendant_focus(self) -> None:
        self.call_after_refresh(self.refresh_command_bar)

    def on_descendant_blur(self) -> None:
        self.call_after_refresh(self.refresh_command_bar)

    def refresh_command_bar(self) -> None:
        for bar in self.screen.query(CommandBar):
            if bar.is_mounted:
                bar.render_suggestions()

    def update_exit_hint(self) -> None:
        for bar in self.screen.query(CommandBar):
            bar.update_hint()

    def cancel_exit(self) -> None:
        if self.exit_timer is not None:
            self.exit_timer.stop()
            self.exit_timer = None
        if self.exit_deadline:
            self.exit_deadline = 0.0
            self.update_exit_hint()

    def action_arm_exit(self) -> None:
        self.cancel_exit()
        if not self.is_home:
            self.show_result_page()
            self.show_home()
        self.exit_deadline = monotonic() + self.EXIT_CONFIRM_SECONDS
        self.exit_timer = self.set_timer(self.EXIT_CONFIRM_SECONDS, self.cancel_exit)
        self.update_exit_hint()

    def action_request_exit(self) -> None:
        if not self.is_home:
            self.action_previous_page()
        elif self.exit_pending:
            if self.process_job is not None:
                try:
                    self.process_job.release()
                except OSError as exc:
                    self.cancel_exit()
                    self.notify(f"Unable to keep the proxy running: {exc}", severity="error")
                    return
            self.exit()
        else:
            self.action_arm_exit()

    def action_complete_or_focus(self) -> None:
        if isinstance(self.focused, CommandInput):
            self.focused.action_complete()
        else:
            self.screen.focus_next()

    def action_previous_page(self) -> None:
        self.cancel_exit()
        if self.screen is not self.result_screen:
            self.pop_screen()
            if self.screen is self.models_screen:
                self.refresh_models_view()
        elif not self.showing_directory:
            self.show_home()
        self.call_after_refresh(self.refresh_command_bar)

    def show_result_page(self) -> None:
        while self.screen is not self.result_screen:
            self.pop_screen()
        self.result_screen.set_focus(self.result_screen.query_one(Input))

    def show_home(self) -> None:
        self.show_commands("Command directory")

    def show_commands(self, title: str) -> None:
        self.showing_directory = True
        self.result_screen.query_one("#result-title", Static).update(title)
        self.result_screen.query_one("#result", Static).update(CommandDirectory())
        self.result_screen.query_one(VerticalScroll).scroll_home(animate=False)
        self.call_after_refresh(self.refresh_command_bar)

    def set_result(self, title: str, content: str) -> None:
        if self.showing_directory:
            return
        self.result_screen.query_one("#result-title", Static).update(Text(title))
        # A pipe read may end halfway through an SGR color sequence. Keep it in
        # result_text for the next chunk, but don't show the unfinished escape.
        complete = re.sub(r"\x1b(?:\[[0-9;]*)?$", "", content)
        self.result_screen.query_one("#result", Static).update(Text.from_ansi(complete))

    async def submit_command(self, value: str) -> None:
        self.cancel_exit()
        try:
            args = shlex.split(value)
        except ValueError as exc:
            self.notify(str(exc), severity="warning")
            return
        if not args:
            return
        args[0] = args[0].removeprefix("/")
        name = args[0]
        if name == "about":
            if len(args) != 1:
                self.notify("/about takes no arguments.", severity="warning")
                return
            self.show_result_page()
            self.show_home()
            self.push_screen(AboutScreen())
            return
        if name == "api" and (len(args) == 1 or args[1:] == ["--show-key"]):
            self.show_result_page()
            self.show_home()
            self.push_screen(ApiScreen(show_key=len(args) > 1))
            return
        if name in {"model", "models"} and len(args) == 1:
            self.show_result_page()
            self.show_home()
            await self.push_screen(self.models_screen)
            self.refresh_models_view()
            if self.refresh_on_start:
                self.start_refresh()
            return
        if name not in COMMANDS and name not in {"quit", "model"}:
            self.notify(
                f"Unknown command: {name}. Type / to see available commands.", severity="warning"
            )
            return
        if self.busy:
            self.notify("A command is running. Wait for it to finish before starting another.")
            return
        if name == "start" and "--foreground" in args:
            self.notify("Use /start here. Run vl start --foreground outside the workspace.")
            return
        self.show_result_page()
        self.busy = True
        self.showing_directory = False
        self.refresh_command_bar()
        self.result_text = ""
        self.set_result(f"/{name} · Running…", "")
        self.run_command(args)

    @work(group="command")
    async def run_command(self, args: list[str]) -> None:
        """Isolate stdout and blocking CLI work; stream device login codes immediately."""
        name = args[0]
        try:
            env = os.environ.copy()
            env.pop(WORKSPACE_JOB_ENV, None)
            if sys.platform == "win32":
                if self.process_job is None:
                    self.process_job = WorkspaceJobs()
                env[WORKSPACE_JOB_ENV] = self.process_job.new_command()
            env.update(
                PYTHONIOENCODING="utf-8",
                NO_COLOR="1",
                TERM="dumb",
                COLUMNS=str(max(24, self.size.width - 8)),
                VELA_LLM_WORKSPACE_COMMAND="1",
            )
            self.process = await asyncio.create_subprocess_exec(
                command_python(env),
                "-u",
                "-c",
                "from process_lifetime import join_workspace_job; join_workspace_job(); "
                "from cli import main; main()",
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            output = CommandOutput()
            while chunk := await self.process.stdout.read(4096):
                self.result_text = output.feed(decoder.decode(chunk))
                self.set_result(f"/{name} · Running…", self.result_text)
                if not self.showing_directory:
                    self.result_screen.query_one(VerticalScroll).scroll_end(animate=False)
            self.result_text = output.feed(decoder.decode(b"", final=True), final=True)
            code = await self.process.wait()
            self.set_result(
                f"/{name} · {'Done' if code == 0 else f'Failed ({code})'}", self.result_text
            )
        except asyncio.CancelledError:
            self.set_result(f"/{name} · Cancelled", self.result_text)
            raise
        except OSError as exc:
            self.set_result(f"/{name} · Failed", str(exc))
        finally:
            if self.process is not None and self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
                await self.process.wait()
            self.process = None
            if self.process_job is not None:
                # Retain jobs with background descendants; completed commands need no handle.
                with suppress(OSError):
                    self.process_job.finish_command()
            self.busy = False
            self.manager.reload()
