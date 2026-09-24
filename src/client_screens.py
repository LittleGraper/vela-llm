"""Harness configuration pages inside the persistent VELA workspace."""

from __future__ import annotations

import asyncio
from typing import Literal

from rich.console import Group
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Input, OptionList, SelectionList, Static
from textual.widgets.option_list import Option

from client_config import CLIENTS, MASK, ClientManager, ClientPlan
from client_health import ClientCheck, check_client
from workspace_screen import WorkspaceScreen

STATUS_COLORS = {
    "Not configured": "#95aab9",
    "Configured": "#9ad68b",
    "Out of date": "#f2c677",
    "Needs review": "#f09999",
}
MESSAGE_COLORS = {
    "info": "#95aab9",
    "success": "#9ad68b",
    "warning": "#f2c677",
}


class ClientsScreen(WorkspaceScreen):
    AUTO_FOCUS = "#clients"
    command_hint = "↑↓ Select · Enter Configure"

    def __init__(self, manager: ClientManager):
        super().__init__()
        self.manager = manager

    def compose_content(self):
        yield Static("Clients", classes="heading")
        yield DataTable(id="clients", cursor_type="row", zebra_stripes=True)

    def on_mount(self):
        self.populate()
        self.query_one(DataTable).focus()

    def on_screen_resume(self):
        if self.is_mounted:
            self.populate()

    def populate(self):
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear(columns=True)
        table.add_column("CLIENT", width=16)
        table.add_column("CONFIG", width=18)
        for client, name in CLIENTS.items():
            status = self.manager.plan(client).status
            table.add_row(
                Text(name, style="bold #d9e4ec"),
                Text(status, style=STATUS_COLORS[status]),
                key=client,
            )
        table.move_cursor(row=row)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self.app.push_screen(ClientConfigScreen(self.manager, event.row_key.value))


class ClientConfigScreen(WorkspaceScreen):
    AUTO_FOCUS = "#client-actions"
    command_hint = "↑↓ Select · Enter Select · P Preview"
    BINDINGS = [Binding("p", "preview", show=False)]
    DEFAULT_CSS = """
    ClientConfigScreen #client-details { height: auto; padding: 1; }
    ClientConfigScreen #client-actions { height: auto; min-height: 4; }
    ClientConfigScreen #client-message { height: auto; padding: 1; }
    """

    def __init__(self, manager: ClientManager, client: str):
        super().__init__()
        self.manager = manager
        self.client = client
        self.plan = manager.plan(client)
        self.removal = manager.plan(client, remove=True)
        self.saving = False
        self.saved = False
        self.message = ""
        self.message_status: Literal["info", "success", "warning"] = "info"
        self.connection: ClientCheck | None = None

    def compose_content(self):
        yield Static(f"Clients / {CLIENTS[self.client]}", classes="heading")
        with VerticalScroll(id="client-scroll"):
            yield Static(id="client-details", markup=False)
            yield OptionList(id="client-actions")
            yield Static(id="client-message", markup=False)

    def on_mount(self):
        self.render_plan()
        self.query_one(OptionList).focus()

    def render_plan(self):
        plan = self.plan
        details = Table.grid(padding=(0, 2), expand=True)
        details.add_column(style="#95aab9", width=12)
        details.add_column(style="#d9e4ec", overflow="fold", ratio=1)
        details.add_row("Config", Text(plan.status, style=STATUS_COLORS[plan.status]))
        connection = Text("Not checked", style=MESSAGE_COLORS["info"])
        if self.connection is not None:
            connection = Text(
                self.connection.message,
                style=MESSAGE_COLORS["success" if self.connection.ok else "warning"],
            )
        details.add_row("Connection", connection)
        for index, path in enumerate(self.manager.paths(self.client)):
            details.add_row("Config file" if index == 0 else "", Text(str(path)))
        details.add_row("Endpoint", Text(plan.endpoint, style="#70dfdf"))
        details.add_row("API key", MASK)
        details.add_row("Models", str(plan.catalog_count))
        self.query_one("#client-details", Static).update(details)
        options = self.query_one(OptionList)
        options.clear_options()
        count = self.plan.catalog_count
        label = f"All ({count})" if self.plan.selected_models is None else f"{count} selected"
        options.add_option(
            Option(Text(f"Available models    {label}", style="#70dfdf"), id="models")
        )
        options.add_option(
            Option("Configure", id="apply", disabled=bool(self.plan.error) or self.saving)
        )
        options.add_option(
            Option(
                "Delete configuration",
                id="remove",
                disabled=not self.removal.has_configuration
                or bool(self.removal.error)
                or self.saving,
            )
        )
        options.add_option(
            Option(
                "Test request (uses model quota)",
                id="test",
                disabled=self.saving or self.manager.plan(self.client).status != "Configured",
            )
        )
        options.highlighted = 0 if self.plan.error else 1
        notice = self.message or self.plan.error
        if not notice:
            self.query_one("#client-message", Static).update(
                Text(
                    f"Switch models directly in {CLIENTS[self.client]}.",
                    style=MESSAGE_COLORS["info"],
                )
            )

        else:
            self.query_one("#client-message", Static).update(
                Text(
                    notice,
                    style=MESSAGE_COLORS[self.message_status if self.message else "warning"],
                )
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        event.prevent_default()
        event.stop()
        if self.saving:
            return
        if event.option.id == "models":
            self.app.push_screen(
                ClientModelsScreen(self.manager, self.client, self.plan.selected_models),
                self.select_models,
            )
        elif event.option.id == "apply":
            self.save(self.plan)
        elif event.option.id == "remove":
            self.save(self.removal)
        elif event.option.id == "test":
            self.test_connection()

    def select_models(self, result):
        if result is None:
            return
        self.plan = self.manager.plan(self.client, selected_models=result["selected_models"])
        self.message = ""
        self.message_status = "info"
        self.saved = False
        self.render_plan()

    @work(exclusive=True)
    async def save(self, plan):
        self.saving = True
        self.saved = False
        self.connection = None
        self.message = "Removing…" if plan.action == "remove" else "Refreshing model capabilities…"
        self.message_status = "info"
        self.render_plan()
        try:
            if plan.action == "apply":
                plan = await asyncio.to_thread(self.manager.refresh_plan, plan)
                if not self.is_mounted:
                    return
            await asyncio.to_thread(self.manager.apply, plan)
        except (OSError, ValueError, RuntimeError) as exc:
            self.message = (
                str(exc)
                if isinstance(exc, (ValueError, RuntimeError))
                else "Could not save client configuration."
            )
            self.message_status = "warning"
        else:
            self.plan = self.manager.plan(self.client)
            self.removal = self.manager.plan(self.client, remove=True)
            self.message = (
                f"VELA configuration removed from {CLIENTS[self.client]}."
                if plan.action == "remove"
                else f"Configured {plan.catalog_count} models. " + self.activation_hint()
            )
            self.message_status = "success"
            if plan.action == "apply":
                self.connection = await asyncio.to_thread(check_client, self.manager, self.client)
            self.saved = True
        finally:
            self.saving = False
            if self.is_mounted:
                self.render_plan()

    @work(exclusive=True)
    async def test_connection(self):
        self.saving = True
        self.saved = False
        self.connection = None
        self.message = "Testing one text request through the proxy…"
        self.message_status = "info"
        self.render_plan()
        try:
            self.connection = await asyncio.to_thread(
                check_client, self.manager, self.client, inference=True
            )
            self.message = self.connection.message
            self.message_status = "success" if self.connection.ok else "warning"
        finally:
            self.saving = False
            if self.is_mounted:
                self.render_plan()

    def activation_hint(self):
        return {
            "dsh": "Switch models in DSH.",
            "kimi": "Restart Kimi, then use /model.",
            "codex": "Restart Codex, then use /model.",
        }[self.client]

    def action_preview(self):
        if isinstance(self.focused, Input) or self.saving:
            return
        self.app.push_screen(ClientPreviewScreen(self.plan, self.manager.settings.local_api_key))


class ModelChecklist(SelectionList):
    BINDINGS = [Binding("enter", "finish", show=False), Binding("a", "all", show=False)]

    def action_finish(self):
        self.screen.finish()

    def action_all(self):
        self.select_all()


class ClientModelsScreen(WorkspaceScreen):
    AUTO_FOCUS = "#client-models"
    command_hint = "↑↓ Select · Space Toggle · A All · Enter Done"

    def __init__(self, manager, client, selected):
        super().__init__()
        self.client = client
        self.names = [entry["name"] for entry in manager.catalog(client)]
        self.selected = selected

    def compose_content(self):
        yield Static(f"Clients / {CLIENTS[self.client]} / Available models", classes="heading")
        selected = set(self.names if self.selected is None else self.selected)
        names = self.names + sorted(selected - set(self.names))
        yield ModelChecklist(
            *[
                (
                    Text(name + (" (unavailable)" if name not in self.names else "")),
                    name,
                    name in selected,
                )
                for name in names
            ],
            id="client-models",
        )

    def finish(self):
        selected = sorted(self.query_one(ModelChecklist).selected)
        self.dismiss({"selected_models": None if set(selected) == set(self.names) else selected})


class ClientPreviewScreen(WorkspaceScreen):
    AUTO_FOCUS = "#client-preview"
    command_hint = "↑↓ Scroll"
    DEFAULT_CSS = "ClientPreviewScreen .preview-file { height: auto; margin-bottom: 1; }"

    def __init__(self, plan: ClientPlan, key: str):
        super().__init__()
        self.plan = plan
        self.key = key

    def compose_content(self):
        yield Static(f"Clients / {CLIENTS[self.plan.client]} / Preview", classes="heading")
        with VerticalScroll(id="client-preview"):
            if self.plan.error:
                yield Static(Text(self.plan.error, style="#f09999"))
            elif self.plan.summary:
                yield Static(
                    Text("\n".join(self.plan.summary), style="#f2c677"), classes="preview-file"
                )
            else:
                yield Static("No changes", classes="preview-file")
            for change in self.plan.changes:
                preview, syntax = change.preview_diff(self.key)
                yield Static(
                    Group(
                        Text(str(change.path), style="bold #70dfdf"),
                        Syntax(preview, syntax, theme="ansi_dark", word_wrap=True),
                    ),
                    classes="preview-file",
                    markup=False,
                )
