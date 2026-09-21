"""Read-only preview of the model preferences currently saved on disk."""

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Static

from config_files import resolve_config_path
from workspace_screen import WorkspaceScreen


class ModelConfigScreen(WorkspaceScreen):
    AUTO_FOCUS = "#config-scroll"
    BINDINGS = [Binding("escape", "app.back", "Back", key_display="Esc", priority=True)]
    DEFAULT_CSS = """
    ModelConfigScreen #config-content { height: auto; padding: 1; }
    ModelConfigScreen #config-scroll { border: round #304754; }
    """

    def compose_content(self) -> ComposeResult:
        path = resolve_config_path(self.app.manager.settings.models_config)
        yield Static("VELA  /  Model configuration · Read only", classes="heading")
        yield Static(Text(str(path)), classes="subtitle")
        with VerticalScroll(id="config-scroll"):
            try:
                source = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                yield Static("No model configuration has been saved yet.", classes="subtitle")
            except (OSError, UnicodeError) as exc:
                yield Static(Text(f"Unable to read configuration: {exc}"), classes="error")
            else:
                yield Static(
                    Syntax(
                        source,
                        "toml",
                        theme="monokai",
                        background_color="#101820",
                        line_numbers=True,
                        word_wrap=True,
                    ),
                    id="config-content",
                )
        yield Footer()
