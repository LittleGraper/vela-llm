from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from github_copilot_patch import apply_github_copilot_oauth_patch


def fetch_available_models() -> list[dict[str, Any]]:
    apply_github_copilot_oauth_patch()

    from litellm.llms.github_copilot.authenticator import Authenticator
    from litellm.llms.github_copilot.common_utils import get_copilot_default_headers

    authenticator = Authenticator()
    api_key = authenticator.get_api_key()
    api_base = (authenticator.get_api_base() or "https://api.githubcopilot.com").rstrip("/")
    response = httpx.get(
        f"{api_base}/models",
        headers=get_copilot_default_headers(api_key),
        timeout=30,
    )
    response.raise_for_status()
    models = response.json().get("data", [])

    registry: list[dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        if model.get("model_picker_enabled") is False:
            continue
        model_id = model.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        endpoints = model.get("supported_endpoints") or []
        if not isinstance(endpoints, list):
            endpoints = []
        entry = {
            "name": model_id,
            "upstream": f"github_copilot/{model_id}",
        }
        limits = _model_limits(model)
        if limits:
            entry.update(limits)
        if "/embeddings" in endpoints:
            entry["mode"] = "embedding"
        elif "/responses" in endpoints and "/chat/completions" not in endpoints:
            entry["mode"] = "responses"
        registry.append(entry)
    return registry


def refresh_model_cache(path: Path) -> list[dict[str, Any]]:
    registry = fetch_available_models()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps({"models": registry}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return registry


def load_model_cache(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    return [model for model in models if isinstance(model, dict)]


def _model_limits(model: dict[str, Any]) -> dict[str, int]:
    capabilities = model.get("capabilities")
    if not isinstance(capabilities, dict):
        return {}
    limits = capabilities.get("limits")
    if not isinstance(limits, dict):
        return {}

    field_map = {
        "max_context_window_tokens": "max_tokens",
        "max_prompt_tokens": "max_input_tokens",
        "max_output_tokens": "max_output_tokens",
    }
    parsed: dict[str, int] = {}
    for source, target in field_map.items():
        value = limits.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            parsed[target] = value
    return parsed
