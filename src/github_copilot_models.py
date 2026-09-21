from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from atomic_files import atomic_write
from github_copilot_patch import apply_github_copilot_oauth_patch


def fetch_available_models() -> list[dict[str, Any]]:
    apply_github_copilot_oauth_patch()

    from litellm.llms.github_copilot.authenticator import Authenticator
    from litellm.llms.github_copilot.common_utils import get_copilot_default_headers

    authenticator = Authenticator()

    def saved_access_token() -> str:
        try:
            token = Path(authenticator.access_token_file).read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if not token:
            raise RuntimeError("Run `vl login` before refreshing the model catalog.")
        return token

    # Discovery must never launch a device-login flow behind an interactive menu.
    authenticator.get_access_token = saved_access_token
    api_key = authenticator.get_api_key()
    api_base = (authenticator.get_api_base() or "https://api.githubcopilot.com").rstrip("/")
    response = httpx.get(
        f"{api_base}/models",
        headers=get_copilot_default_headers(api_key),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ValueError("Invalid model catalog: expected a data array.")

    registry: list[dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("Invalid model catalog entry.")
        if model.get("model_picker_enabled") is False:
            continue
        model_id = model.get("id")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("Invalid model catalog: missing model ID.")
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
        for key in ("capabilities", "billing"):
            if isinstance(model.get(key), dict):
                entry[key] = model[key]
        if endpoints:
            entry["supported_endpoints"] = endpoints
        entry.update(_context_options(model, limits))
        if "/embeddings" in endpoints:
            entry["mode"] = "embedding"
        elif "/responses" in endpoints and "/chat/completions" not in endpoints:
            entry["mode"] = "responses"
        registry.append(entry)
    return registry


def refresh_model_cache(path: Path) -> list[dict[str, Any]]:
    registry = fetch_available_models()
    if not registry or not _valid_registry(registry):
        raise ValueError("Empty or invalid model catalog; keeping the last successful cache.")
    atomic_write(
        path,
        json.dumps(
            {"models": registry, "fetched_at": datetime.now(UTC).isoformat()},
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return registry


def load_model_cache(path: Path) -> list[dict[str, Any]]:
    return load_catalog(path).get("models", [])


def load_catalog(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not _valid_registry(models):
        return {}
    return data


def _valid_registry(models: Any) -> bool:
    if not isinstance(models, list) or not models:
        return False
    if not all(
        isinstance(model, dict)
        and isinstance(model.get("name"), str)
        and model["name"]
        and isinstance(model.get("upstream"), str)
        and model["upstream"]
        for model in models
    ):
        return False
    return len({model["name"] for model in models}) == len(models)


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


def _context_options(model: dict[str, Any], limits: dict[str, int]) -> dict[str, Any]:
    billing = model.get("billing")
    prices = billing.get("token_prices") if isinstance(billing, dict) else None
    default = prices.get("default") if isinstance(prices, dict) else None
    if not isinstance(default, dict):
        return {}
    value = default.get("max_prompt_tokens", default.get("context_max"))
    maximum = limits.get("max_input_tokens")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or not maximum:
        return {}
    if value > maximum:
        return {}
    # Match the editor's input choices. Billing's long_context limit is kept
    # separately in the unmodified billing metadata, not used as a capability cap.
    return {
        "default_context_size": value,
        "context_size_options": sorted({value, maximum}),
    }
