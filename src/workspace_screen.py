"""Shared frame for persistent CLI pages, also usable by the standalone model menu."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen


class WorkspaceScreen(Screen):
    def compose(self) -> ComposeResult:
        if getattr(self.app, "persistent_workspace", False):
            from shell_app import CommandBar, WorkspaceHeader

            yield WorkspaceHeader()
            yield CommandBar()
            with Vertical(classes="workspace-body"):
                yield from self.compose_content()
        else:
            yield from self.compose_content()

    def compose_content(self) -> ComposeResult:
        return
        yield
