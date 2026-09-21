from __future__ import annotations

import tomllib
from pathlib import Path

import tomlkit

from atomic_files import atomic_write, config_lock


def load_default_model(path: Path) -> str:
    with path.open("rb") as config_file:
        data = tomllib.load(config_file)
    models = data.get("models", {})
    default = models.get("default")
    if not isinstance(default, str) or not default:
        return "gpt-4"
    return default


def write_default_model(path: Path, default: str) -> None:
    with config_lock(path):
        document = _document(path)
        document.setdefault("models", tomlkit.table())["default"] = default
        atomic_write(path, tomlkit.dumps(document))


def write_context(path: Path, name: str, mode: str, size: int | None = None) -> None:
    if mode not in {"auto", "maximum", "custom"}:
        raise ValueError("Unknown context mode.")
    if mode == "custom" and (type(size) is not int or size <= 0):
        raise ValueError("Custom context requires a positive token count.")
    with config_lock(path):
        document = _document(path)
        models = document.setdefault("models", tomlkit.table())
        contexts = models.setdefault("context", tomlkit.table())
        if mode == "auto":
            contexts.pop(name, None)
            if not contexts:
                models.pop("context", None)
        else:
            value = {"mode": mode}
            if mode == "custom":
                value["size"] = size
            contexts[name] = value
        atomic_write(path, tomlkit.dumps(document))


def _document(path: Path):
    if not path.exists():
        return tomlkit.document()
    return tomlkit.parse(path.read_text(encoding="utf-8"))
