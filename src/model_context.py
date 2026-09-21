from __future__ import annotations

from typing import Any


def positive_tokens(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def context_options(entry: dict[str, Any]) -> list[int]:
    options = entry.get("context_size_options", [])
    if not isinstance(options, list):
        return []
    maximum = positive_tokens(entry.get("max_input_tokens"))
    return sorted({v for v in options if positive_tokens(v) and (maximum is None or v <= maximum)})


def resolve_context(entry: dict[str, Any], preference: Any = None) -> dict[str, Any]:
    """Keep upstream limits intact and publish the separately resolved preference."""
    preference = {} if preference is None else preference
    options = context_options(entry)
    maximum = max(options) if options else positive_tokens(entry.get("max_input_tokens"))
    default = positive_tokens(entry.get("default_context_size"))
    if default and maximum and default > maximum:
        default = None
    mode = preference.get("mode", "auto") if isinstance(preference, dict) else "invalid"
    if not isinstance(mode, str):
        mode = "invalid"
    configured = preference.get("size") if isinstance(preference, dict) else None
    status = "ok"
    effective = default
    if mode == "maximum":
        effective = maximum
    elif mode == "custom":
        if positive_tokens(configured) and configured in options:
            effective = configured
        else:
            status = "needs_review"
    elif mode != "auto":
        status = "needs_review"
    if effective is None and status == "ok":
        status = "unknown"
    return {
        "context_mode": mode,
        "configured_context_size": configured if mode == "custom" else None,
        "context_size": effective,
        "context_status": status,
    }


def with_context(registry: list[dict[str, Any]], preferences: dict) -> list[dict[str, Any]]:
    return [
        {**entry, **resolve_context(entry, preferences.get(entry["name"]))} for entry in registry
    ]


def format_tokens(value: Any) -> str:
    if not positive_tokens(value):
        return "Unknown"
    return f"{value // 1000:,}K" if value % 1000 == 0 else f"{value:,}"
