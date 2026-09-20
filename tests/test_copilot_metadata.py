from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from github_copilot_models import _context_options, fetch_available_models, refresh_model_cache
from main import app
from settings import get_settings


def test_model_metadata_survives_fetch_cache_and_public_endpoint(monkeypatch, tmp_path) -> None:
    model = {
        "id": "gpt-6-astra",
        "supported_endpoints": ["/responses"],
        "capabilities": {
            "limits": {
                "max_context_window_tokens": 1178000,
                "max_prompt_tokens": 1050000,
                "max_output_tokens": 128000,
            }
        },
        "billing": {
            "token_prices": {
                "default": {"max_prompt_tokens": 272000, "input_price": 1000},
                "long_context": {"max_prompt_tokens": 872000, "input_price": 2000},
            }
        },
    }
    from litellm.llms.github_copilot.authenticator import Authenticator

    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setattr(Authenticator, "get_api_key", lambda self: "test-token")
    monkeypatch.setattr(Authenticator, "get_api_base", lambda self: "https://example.test")

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [model]}

    def fetch(url, headers, timeout):
        assert url == "https://example.test/models"
        assert headers["x-github-api-version"] == "2026-08-01"
        return Response()

    monkeypatch.setattr("github_copilot_models.httpx.get", fetch)
    assert fetch_available_models()[0]["max_input_tokens"] == 1050000
    cache = tmp_path / "models-cache.json"
    refresh_model_cache(cache)
    assert json.loads(cache.read_text())["models"][0]["billing"] == model["billing"]
    monkeypatch.setenv("VELA_LLM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("VELA_LLM_MODELS_CONFIG", str(tmp_path / "models.toml"))
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        result = TestClient(app).get("/v1/models", headers={"Authorization": "Bearer test-key"})
        assert result.status_code == 200
        entry = result.json()["data"][0]
        assert entry["capabilities"] == model["capabilities"]
        assert entry["billing"] == model["billing"]
        assert entry["context_size_options"] == [272000, 1050000]
        assert entry["default_context_size"] == 272000
        assert entry["supported_endpoints"] == ["/responses"]
        assert entry["max_tokens"] == 1178000
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("value", [True, -1, 0, "272000", 2000000, None])
def test_invalid_context_tier_does_not_invent_options(value) -> None:
    model = {"billing": {"token_prices": {"default": {"max_prompt_tokens": value}}}}
    assert _context_options(model, {"max_input_tokens": 1050000}) == {}
