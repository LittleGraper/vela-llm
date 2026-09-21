from __future__ import annotations

from typing import Any


def register_litellm_model_metadata(litellm, registry: list[dict[str, Any]]) -> None:
    model_cost: dict[str, dict[str, Any]] = {}
    for entry in registry:
        mode = entry.get("mode")
        limits = {
            key: entry[key]
            for key in ("max_tokens", "max_input_tokens", "max_output_tokens")
            if key in entry
        }
        # Copilot's context choices are prompt budgets, not total input + output windows.
        context_size = entry.get("context_size")
        if type(context_size) is int and context_size > 0:
            limits["max_input_tokens"] = context_size
        if not mode and not limits:
            continue
        model_cost[entry["upstream"]] = {
            "litellm_provider": "github_copilot",
            **limits,
        }
        if mode:
            model_cost[entry["upstream"]]["mode"] = mode
    if model_cost:
        litellm.register_model(model_cost)
