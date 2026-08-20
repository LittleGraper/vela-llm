from __future__ import annotations

import httpx

from github_copilot_patch import apply_github_copilot_oauth_patch


def fetch_available_models() -> list[dict[str, str]]:
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

    registry: list[dict[str, str]] = []
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
        if "/embeddings" in endpoints:
            entry["mode"] = "embedding"
        elif "/responses" in endpoints and "/chat/completions" not in endpoints:
            entry["mode"] = "responses"
        registry.append(entry)
    return registry
