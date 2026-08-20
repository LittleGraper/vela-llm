from __future__ import annotations

import tomllib
from pathlib import Path


def load_default_model(path: Path) -> str:
    with path.open("rb") as config_file:
        data = tomllib.load(config_file)
    models = data.get("models", {})
    default = models.get("default")
    if not isinstance(default, str) or not default:
        return "gpt-4"
    return default


def write_default_model(path: Path, default: str) -> None:
    path.write_text(f'[models]\ndefault = "{_escape(default)}"\n', encoding="utf-8")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
