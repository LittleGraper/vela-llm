from __future__ import annotations

from datetime import datetime
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from cli_ui import heading, make_console
from config_files import resolve_config_path
from github_copilot_models import load_catalog
from model_context import context_options, format_tokens, resolve_context
from model_store import write_context, write_default_model


class ModelManager:
    """Model preferences and snapshots shared by the TUI and plain output."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.reload()

    def reload(self) -> None:
        self.catalog = load_catalog(self.settings.model_cache_path)
        self.registry = self.catalog.get("models", [])
        self.preferences = self.settings.context_preferences()
        self.default = self.settings.default_model

    def entries(self) -> list[dict[str, Any]]:
        return (
            self.registry
            if self.catalog
            else [{"name": name} for name in sorted(set(self.preferences) | {self.default})]
        )

    def entry(self, name: str) -> dict[str, Any]:
        return next((entry for entry in self.registry if entry["name"] == name), {"name": name})

    def state(self, name: str) -> dict[str, Any]:
        return resolve_context(self.entry(name), self.preferences.get(name))

    def unavailable(self) -> list[str]:
        if not self.catalog:
            return []
        names = {entry["name"] for entry in self.registry}
        return sorted((set(self.preferences) | {self.default}) - names)

    def updated_at(self) -> str:
        try:
            return (
                datetime.fromisoformat(self.catalog.get("fetched_at"))
                .astimezone()
                .strftime("%Y-%m-%d %H:%M:%S")
            )
        except (TypeError, ValueError):
            return "Unknown"

    def set_default(self, name: str) -> None:
        latest = load_catalog(self.settings.model_cache_path).get("models", [])
        if name not in {entry["name"] for entry in latest}:
            raise ValueError("Refresh model availability before setting default.")
        write_default_model(resolve_config_path(self.settings.models_config), name)
        self.reload()

    def delete_context(self, name: str) -> list[str]:
        write_context(resolve_config_path(self.settings.models_config), name, "auto")
        self.reload()
        return self.sync_clients()

    def save_context(self, name: str, mode: str, size: int | None = None) -> list[str]:
        # Re-read the latest successful catalog, including refreshes by another CLI.
        latest = load_catalog(self.settings.model_cache_path).get("models", [])
        entry = next((e for e in latest if e["name"] == name), None)
        if mode != "auto" and entry is None:
            raise ValueError("Model is unavailable. Refresh the catalog and try again.")
        if mode == "custom" and size not in context_options(entry):
            raise ValueError("This size is no longer available. Go back and reopen the model.")
        if mode == "maximum" and resolve_context(entry, {"mode": mode})["context_size"] is None:
            raise ValueError("Upstream maximum is Unknown; cannot save Maximum.")
        write_context(resolve_config_path(self.settings.models_config), name, mode, size)
        self.reload()
        return self.sync_clients()

    def sync_clients(self) -> list[str]:
        from client_config import sync_clients_after_refresh

        results = sync_clients_after_refresh(self.settings)
        if any("models synced" in result for result in results):
            results.append("Restart Codex/DSH/Kimi to use the updated context.")
        return results


def model_cells(manager: ModelManager, entry: dict) -> tuple[Text, ...]:
    name = entry["name"]
    state = manager.state(name)
    label = Text(name)
    if name == manager.default:
        label.append(" (default)", style="cyan")
    review = state["context_status"] == "needs_review"
    return (
        label,
        Text(entry.get("mode", "chat")),
        Text(state["context_mode"].title(), style="yellow" if review else ""),
        Text(format_tokens(state["context_size"]), style="bold cyan"),
        Text("Needs review" if review else "", style="yellow"),
    )


def print_model_snapshot(settings, console: Console | None = None) -> None:
    console = console or make_console()
    heading("Models", console=console)
    manager = ModelManager(settings)
    table = Table(box=box.SIMPLE_HEAD, header_style="bold #70dfdf", expand=False)
    for name in ("MODEL", "API", "MODE", "CONTEXT", "STATUS"):
        table.add_column(name, overflow="fold")
    for entry in manager.entries():
        table.add_row(*model_cells(manager, entry))
    console.print(table)
    console.print(Text(f"Catalog updated: {manager.updated_at()}", style="dim"))
    if not manager.catalog:
        console.print("No saved catalog. Open `vl models` in a terminal to refresh.")
    for name in manager.unavailable():
        console.print(Text(f"Unavailable: {name} (saved preference retained)", style="yellow"))
    if manager.default in manager.unavailable():
        console.print(
            "Default model unavailable. Open `vl models` to select another.", style="yellow"
        )
