from __future__ import annotations


def register_litellm_model_metadata(litellm, registry: list[dict[str, str]]) -> None:
    model_cost: dict[str, dict[str, str]] = {}
    for entry in registry:
        mode = entry.get("mode")
        if not mode:
            continue
        model_cost[entry["upstream"]] = {
            "litellm_provider": "github_copilot",
            "mode": mode,
        }
    if model_cost:
        litellm.register_model(model_cost)
