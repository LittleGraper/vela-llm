"""Shared presentation for command output; interactive pages live in model_app."""

from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme(
    {
        "vela.accent": "bold #70dfdf",
        "vela.muted": "#95aab9",
        "vela.value": "bold #d9e4ec",
        "vela.success": "#9ad68b",
        "vela.warning": "#f2c677",
        "vela.error": "bold #ff8796",
        "vela.border": "#304754",
    }
)


def make_console(*, file: TextIO | None = None, stderr: bool = False) -> Console:
    if os.environ.get("VELA_LLM_WORKSPACE_COMMAND") == "1":
        # This pipe carries Rich colors into Textual; it is not a terminal driver.
        return Console(
            file=file,
            stderr=stderr,
            theme=THEME,
            highlight=False,
            force_terminal=True,
            color_system="truecolor",
            no_color=False,
            legacy_windows=False,
        )
    return Console(file=file, stderr=stderr, theme=THEME, highlight=False)


def output(text: str, *, style: str = "", stderr: bool = False) -> None:
    make_console(stderr=stderr).print(Text(text, style=style))


def heading(title: str, description: str | None = None, *, console: Console | None = None) -> None:
    console = console or make_console()
    console.print()
    console.rule(Text(f"VELA / {title}", style="vela.accent"), align="left", style="vela.border")
    if description:
        console.print(Text(description, style="vela.muted"))
    console.print()


def message(text: str, *, level: str = "info", stderr: bool = False) -> None:
    label, style = {
        "info": ("INFO", "vela.muted"),
        "success": ("OK", "vela.success"),
        "warning": ("WARN", "vela.warning"),
        "error": ("ERROR", "vela.error"),
    }[level]
    line = Text(f"{label}  ", style=style)
    line.append(text)
    make_console(stderr=stderr).print(line)


def fields(title: str, rows: list[tuple[str, object]], *, console: Console | None = None) -> None:
    console = console or make_console()
    table = Table.grid(padding=(0, 2))
    table.add_column(style="vela.muted")
    table.add_column(style="vela.value", overflow="fold")
    for label, value in rows:
        table.add_row(Text(label), value if isinstance(value, Text) else Text(str(value)))
    console.print(
        Panel(
            table,
            title=Text(title, style="vela.accent"),
            title_align="left",
            border_style="vela.border",
            padding=(1, 2),
            width=min(console.width, 88),
        )
    )


@contextmanager
def activity(text: str):
    console = make_console()
    if (
        os.environ.get("VELA_LLM_WORKSPACE_COMMAND") != "1"
        and console.is_terminal
        and not console.is_dumb_terminal
    ):
        with console.status(Text(text, style="vela.muted"), spinner="dots"):
            yield
    else:
        yield


def help_table(console: Console, title: str, rows: list[tuple[str, str]]) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="vela.accent", min_width=16)
    table.add_column(style="vela.muted")
    for label, detail in rows:
        table.add_row(Text(label), Text(detail))
    console.print(Text(title, style="bold"))
    console.print(table)
    console.print()


class VlArgumentParser(argparse.ArgumentParser):
    """Keep argparse parsing and exit codes, render every help page with Rich."""

    def print_help(self, file: TextIO | None = None) -> None:
        console = make_console(file=file)
        heading("Commands" if self.prog == "vl" else self.prog, self.description, console=console)
        subcommands = next(
            (a for a in self._actions if isinstance(a, argparse._SubParsersAction)), None
        )
        if subcommands is not None:
            groups = {
                "Proxy": ("start", "stop", "quit"),
                "Configuration": ("model", "models", "api", "test"),
                "Account": ("login", "whoami", "logout"),
                "Tools": ("update", "version", "help"),
            }
            actions = subcommands._choices_actions
            rendered: set[str] = set()
            for title, names in groups.items():
                rows = []
                for name in names:
                    action = next((a for a in actions if a.dest == name), None)
                    if action is not None and action.dest not in rendered:
                        aliases = [a.dest for a in actions if a.help == action.help]
                        rendered.update(aliases)
                        rows.append((" / ".join(aliases), action.help or ""))
                if rows:
                    help_table(console, title, rows)
            remaining = [(a.dest, a.help or "") for a in actions if a.dest not in rendered]
            if remaining:
                help_table(console, "Other commands", remaining)
        rows = []
        for action in self._actions:
            if action.option_strings and action.help != argparse.SUPPRESS:
                label = ", ".join(action.option_strings)
                if action.nargs != 0:
                    label += f" {action.metavar or action.dest.upper()}"
                rows.append((label, action.help or ""))
        if rows:
            help_table(console, "Options", rows)
        if self.prog == "vl":
            console.print(Text("Use vl <command> --help for command options.", style="vela.muted"))

    def error(self, message_text: str) -> None:
        message(message_text, level="error", stderr=True)
        output(f"Run `{self.prog} --help` for available options.", stderr=True)
        self.exit(2)
