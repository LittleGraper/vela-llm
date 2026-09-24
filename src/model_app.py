from __future__ import annotations

import asyncio
from concurrent.futures import Future
from threading import Thread

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Button, DataTable, Footer, OptionList, Static
from textual.widgets.option_list import Option

from github_copilot_models import refresh_model_cache
from model_config_screen import ModelConfigScreen
from model_context import context_options, format_tokens, resolve_context
from model_menu import ModelManager, model_cells
from workspace_screen import WorkspaceScreen


class ModelTable(DataTable):
    BINDINGS = [
        Binding("up", "cursor_up", "Select", key_display="↑↓"),
        Binding("enter", "select_cursor", "Edit", key_display="Enter"),
    ]


class Choices(OptionList):
    BINDINGS = [
        Binding("up", "cursor_up", "Select", key_display="↑↓"),
        Binding("enter", "select", "Edit", key_display="Enter"),
    ]


class ModelsScreen(WorkspaceScreen):
    AUTO_FOCUS = "DataTable"
    BINDINGS = [
        Binding("escape", "app.back", "Exit", key_display="Esc", priority=True),
        Binding("d", "set_default", "Set default"),
        Binding("r", "refresh_catalog", "Refresh"),
        Binding("p", "preview_config", "Preview config"),
    ]

    def compose_content(self) -> ComposeResult:
        yield Static("VELA  /  Models", classes="heading")
        yield ModelTable(id="models", cursor_type="row", zebra_stripes=True)
        yield Button("Unavailable models", id="unavailable", variant="warning")
        yield Static(id="catalog-status", classes="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        self.populate()
        table.focus()

    def on_resize(self) -> None:
        if self.query(DataTable):
            self.populate()

    def selected_model(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count:
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return None

    def populate(self) -> None:
        manager = self.app.manager
        table = self.query_one(DataTable)
        name = self.selected_model() or manager.default
        previous_row = table.cursor_row
        table.clear(columns=True)
        width = max(30, table.size.width or self.size.width - 4)
        wide = width >= 80
        columns = [(0, "MODEL", 0)]
        if wide:
            columns.append((1, "API", 9))
        columns.extend([(2, "MODE", 7), (3, "CONTEXT", 8)])
        if wide:
            columns.append((4, "STATUS", 12))
        model_width = width - sum(size for _, _, size in columns) - 2 * len(columns) - 2
        for index, label, size in columns:
            table.add_column(label, key=str(index), width=size or max(8, model_width))
        entries = manager.entries()
        for entry in entries:
            cells = model_cells(manager, entry)
            if not wide and manager.state(entry["name"])["context_status"] == "needs_review":
                cells[2].plain = "Review"
            cells[0].truncate(max(8, model_width), overflow="ellipsis")
            table.add_row(*(cells[index] for index, _, _ in columns), key=entry["name"])
        selected = next(
            (i for i, entry in enumerate(entries) if entry["name"] == name),
            min(previous_row, max(0, len(entries) - 1)),
        )
        table.move_cursor(row=selected)
        unavailable = manager.unavailable()
        button = self.query_one("#unavailable", Button)
        button.display = bool(unavailable)
        button.label = f"Unavailable models ({len(unavailable)})"
        self.update_status()

    def update_status(self) -> None:
        manager = self.app.manager
        status = Text(f"Catalog updated: {manager.updated_at()}", style="dim")
        if manager.default in manager.unavailable():
            status.append("\nDefault unavailable. Select a model and press D.", style="yellow")
        elif not manager.catalog:
            status.append("\nNo saved catalog. Refresh to load available context options.")
        if self.app.notice:
            status.append(f"\n{self.app.notice}")
        self.query_one("#catalog-status", Static).update(status)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(ContextScreen(event.row_key.value))

    def action_set_default(self) -> None:
        if name := self.selected_model():
            try:
                self.app.manager.set_default(name)
            except (OSError, ValueError) as exc:
                self.app.notice = str(exc)
            else:
                self.app.notice = f"Default model saved: {name}"
            self.populate()

    def action_refresh_catalog(self) -> None:
        self.app.start_refresh()

    def action_preview_config(self) -> None:
        self.app.push_screen(ModelConfigScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "unavailable":
            self.app.push_screen(UnavailableScreen())


class ChoiceScreen(WorkspaceScreen):
    AUTO_FOCUS = "OptionList"
    BINDINGS = [
        Binding("escape", "app.back", "Back", key_display="Esc", priority=True),
    ]

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def show_error(self, exc: Exception) -> None:
        self.query_one(".error", Static).update(Text(str(exc), style="yellow"))

    def save(self, name: str, mode: str, size: int | None = None) -> None:
        try:
            results = self.app.manager.save_context(name, mode, size)
        except (OSError, ValueError) as exc:
            self.show_error(exc)
        else:
            self.app.notice = f"Saved {name}: {mode.title()}"
            if results:
                self.app.notice += "\n" + " · ".join(results)
            self.app.return_to_models()


class ContextScreen(ChoiceScreen):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.model_name = name

    def compose_content(self) -> ComposeResult:
        manager = self.app.manager
        entry = manager.entry(self.model_name)
        state = manager.state(self.model_name)
        auto = format_tokens(resolve_context(entry)["context_size"])
        maximum = format_tokens(resolve_context(entry, {"mode": "maximum"})["context_size"])
        yield Static(Text(f"VELA  /  Models  /  {self.model_name}"), classes="heading")
        yield Static("Context", classes="subtitle")
        with Vertical(classes="card"):
            yield Static(
                f"Current: {state['context_mode'].title()}  ·  "
                f"Effective context: {format_tokens(state['context_size'])}",
                classes="summary",
                markup=False,
            )
            if state["context_status"] == "needs_review":
                yield Static(
                    "Needs review: saved preference is unavailable. Using Auto until corrected.",
                    classes="warning",
                )
            labels = [
                ("auto", f"Auto       {auto}  ·  Follow upstream default"),
                ("maximum", f"Maximum    {maximum}  ·  Follow upstream maximum"),
                ("custom", "Custom     Choose an available context size"),
            ]
            options = Choices(
                *[
                    Option(
                        Text(label + ("  [saved]" if state["context_mode"] == mode else "")),
                        id=mode,
                    )
                    for mode, label in labels
                ],
                id="modes",
            )
            options.highlighted = next(
                (i for i, (mode, _) in enumerate(labels) if state["context_mode"] == mode), 0
            )
            yield options
            billing = entry.get("billing", {})
            prices = billing.get("token_prices", {}) if isinstance(billing, dict) else {}
            if isinstance(prices, dict) and prices.get("long_context"):
                yield Static(
                    "Long-context pricing applies; see upstream billing.", classes="warning"
                )
            yield Static("Enter saves Auto / Maximum or opens Custom.", classes="hint")
            yield Static("", classes="error")
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id == "custom":
            self.app.push_screen(CustomScreen(self.model_name))
        else:
            self.save(self.model_name, event.option_id)


class CustomScreen(ChoiceScreen):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.model_name = name

    def compose_content(self) -> ComposeResult:
        manager = self.app.manager
        sizes = context_options(manager.entry(self.model_name))
        current = manager.state(self.model_name)["configured_context_size"]
        yield Static(Text(f"VELA  /  {self.model_name}  /  Custom"), classes="heading")
        back = "Esc"
        yield Static(f"Choose one context size. Enter saves; {back} discards.", classes="subtitle")
        with Vertical(classes="card"):
            options = Choices(
                *[
                    Option(
                        Text(
                            f"{format_tokens(size)} tokens"
                            + ("  [saved]" if size == current else "")
                        ),
                        id=str(size),
                    )
                    for size in sizes
                ],
                id="sizes",
            )
            if current in sizes:
                options.highlighted = sizes.index(current)
            yield options
            if not sizes:
                yield Static(f"No context options published. Press {back} to return and refresh.")
            yield Static("", classes="error")
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.save(self.model_name, "custom", int(event.option_id))


class UnavailableScreen(ChoiceScreen):
    def compose_content(self) -> ComposeResult:
        yield Static("VELA  /  Unavailable models", classes="heading")
        yield Static("Saved preferences are retained until you delete them.", classes="subtitle")
        with Vertical(classes="card"):
            yield Choices(*[Option(Text(name), id=name) for name in self.app.manager.unavailable()])
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.app.push_screen(UnavailableDetailScreen(event.option_id))


class UnavailableDetailScreen(ChoiceScreen):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.model_name = name

    def compose_content(self) -> ComposeResult:
        manager = self.app.manager
        preference = manager.preferences.get(self.model_name)
        yield Static(Text(f"VELA  /  Unavailable  /  {self.model_name}"), classes="heading")
        with Vertical(classes="card"):
            yield Static(Text(f"Saved context: {preference or 'Auto (no override)'}"))
            if self.model_name == manager.default:
                yield Static(
                    "Default model unavailable. Return to Models and press D on another.",
                    classes="warning",
                )
            yield Choices(Option("Delete saved context", id="delete", disabled=not preference))
            yield Static("Only Enter deletes this context preference.", classes="hint")
            yield Static("", classes="error")
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        try:
            results = self.app.manager.delete_context(self.model_name)
        except (OSError, ValueError) as exc:
            self.show_error(exc)
        else:
            self.app.notice = f"Deleted saved context: {self.model_name}"
            if results:
                self.app.notice += "\n" + " · ".join(results)
            self.app.return_to_models()


class ModelApp(App, inherit_bindings=False):
    """Textual owns input, layout, scrolling, and terminal restoration."""

    TITLE = "VELA Models"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+c", "quit", show=False, priority=True)]
    CSS = """
    Screen { background: #101820; color: #d9e4ec; padding: 1 2 0 2; }
    Screen:inline { height: 26; }
    .heading { color: #70dfdf; text-style: bold; height: auto; margin-bottom: 1; }
    .subtitle { color: #95aab9; height: auto; margin-bottom: 1; }
    DataTable { height: 1fr; min-height: 3; background: #101820; }
    DataTable > .datatable--header { background: #20313f; color: #aec3d2; }
    DataTable > .datatable--cursor { background: #245369; color: #ffffff; text-style: bold; }
    DataTable > .datatable--odd-row { background: #15222d; }
    DataTable > .datatable--even-row { background: #101820; }
    .status { height: auto; max-height: 4; margin-top: 1; color: #95aab9; }
    .card { height: 1fr; border: round #304754; padding: 1 2; overflow-y: auto; }
    .summary { height: auto; margin-bottom: 1; }
    OptionList { height: auto; max-height: 100%; background: transparent; border: none; }
    OptionList > .option-list--option { padding: 0 1; }
    OptionList > .option-list--option-highlighted { background: #245369; color: #ffffff; }
    OptionList > .option-list--option-hover { background: #20313f; }
    .warning, .error { color: #f2c677; height: auto; }
    .hint { color: #95aab9; height: auto; margin-top: 1; }
    #unavailable { height: 3; margin-top: 1; }
    Footer { background: #20313f; margin-top: 1; }
    Footer > .footer-key--key { background: #304754; color: #70dfdf; }
    """

    def __init__(self, settings, *, refresh_on_start: bool = True) -> None:
        super().__init__()
        self.manager = ModelManager(settings)
        self.refresh_on_start = refresh_on_start
        self.refreshing = False
        self.notice = ""
        self.models_screen = ModelsScreen()

    def on_mount(self) -> None:
        self.push_screen(self.models_screen)
        if self.refresh_on_start:
            self.call_after_refresh(self.start_refresh)

    def action_back(self) -> None:
        if self.screen is self.models_screen:
            self.exit()
        else:
            self.pop_screen()
            if self.screen is self.models_screen:
                self.refresh_models_view()

    def return_to_models(self) -> None:
        while self.screen is not self.models_screen:
            self.pop_screen()
        self.refresh_models_view()

    def refresh_models_view(self) -> None:
        self.manager.reload()
        self.models_screen.populate()
        self.models_screen.query_one(DataTable).focus()

    def start_refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.notice = "Refreshing model catalog…"
        if self.models_screen.is_mounted:
            self.models_screen.update_status()
        self.refresh_catalog()

    @work(group="catalog")
    async def refresh_catalog(self) -> None:
        # The upstream authenticator is synchronous. A daemon bridge lets Textual
        # cancel the waiting worker on Esc without joining a blocked HTTP thread.
        # Only this async worker touches widgets; the thread writes the atomic cache.
        result: Future = Future()
        path = self.manager.settings.model_cache_path

        def fetch() -> None:
            try:
                from client_config import sync_clients_after_refresh

                refresh_model_cache(path)
                result.set_result(sync_clients_after_refresh(self.manager.settings))
            except Exception as exc:
                result.set_exception(exc)

        Thread(target=fetch, name="vela-model-catalog", daemon=True).start()
        try:
            while not result.done():
                await asyncio.sleep(0.05)
            sync_results = result.result()
        except Exception as exc:
            self.notice = f"Refresh failed: {' '.join(str(exc).split())[:180]}"
        else:
            self.notice = "Catalog refreshed."
            if sync_results:
                self.notice += "\n" + " · ".join(sync_results)
        finally:
            self.refreshing = False
        if self.screen is self.models_screen:
            self.refresh_models_view()
