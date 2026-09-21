"""Connection settings with an explicit, page-local key reveal control."""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Input, Static

from workspace_screen import WorkspaceScreen


class ApiScreen(WorkspaceScreen):
    AUTO_FOCUS = "#toggle-key"
    BINDINGS = [Binding("k", "toggle_key", show=False)]
    DEFAULT_CSS = """
    ApiScreen .api-card {
        height: auto; max-width: 88; border: round #304754;
        padding: 1 2; margin-bottom: 1; border-title-color: #70dfdf;
    }
    ApiScreen .api-label { height: auto; color: #95aab9; }
    ApiScreen .api-value { height: auto; color: #d9e4ec; text-style: bold; }
    ApiScreen #api-key { margin: 1 0; color: #70dfdf; }
    ApiScreen #toggle-key { min-width: 14; height: 3; background: #20313f; }
    ApiScreen #toggle-key:focus { background: #245369; text-style: bold; }
    """

    def __init__(self, *, show_key: bool = False) -> None:
        super().__init__()
        self.key_visible = show_key

    def compose_content(self) -> ComposeResult:
        from cli import local_api_urls

        root_url, openai_url = local_api_urls(self.app.manager.settings)
        yield Static("VELA  /  API settings", classes="heading")
        with VerticalScroll():
            with Vertical(classes="api-card", id="api-endpoints"):
                yield Static("OpenAI Compatible", classes="api-label")
                yield Static(Text(openai_url), classes="api-value")
                yield Static("\nAnthropic Compatible", classes="api-label")
                yield Static(Text(root_url), classes="api-value")
            with Vertical(classes="api-card", id="api-credentials"):
                yield Static("Shared by both APIs", classes="api-label")
                yield Static(id="api-key", classes="api-value", markup=False)
                yield Button("Show key", id="toggle-key")

    def on_mount(self) -> None:
        self.query_one("#api-endpoints").border_title = "Base URLs"
        self.query_one("#api-credentials").border_title = "API Key"
        self.update_key()

    def on_resize(self) -> None:
        self.call_after_refresh(self.keep_control_visible)

    def keep_control_visible(self) -> None:
        if isinstance(self.focused, Button):
            self.focused.scroll_visible(animate=False)

    def update_key(self) -> None:
        from cli import mask_api_key

        key = self.app.manager.settings.local_api_key
        self.query_one("#api-key", Static).update(
            Text((key if self.key_visible else mask_api_key(key)) if key else "Not configured")
        )
        button = self.query_one("#toggle-key", Button)
        button.label = "Hide key" if self.key_visible else "Show key"
        button.disabled = not key
        if self.app.screen is self:
            self.app.call_after_refresh(self.app.refresh_command_bar)

    def action_toggle_key(self) -> None:
        if isinstance(self.focused, Input) or not self.app.manager.settings.local_api_key:
            return
        self.key_visible = not self.key_visible
        self.update_key()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "toggle-key":
            event.stop()
            self.action_toggle_key()

    def on_screen_suspend(self) -> None:
        self.key_visible = False
        if self.is_mounted:
            self.update_key()
