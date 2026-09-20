from __future__ import annotations

import json

import github_copilot_models


def test_fetch_available_models_maps_supported_endpoints(monkeypatch) -> None:
    class FakeAuthenticator:
        def get_api_key(self) -> str:
            return "api-key"

        def get_api_base(self) -> str:
            return "https://example.test"

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {
                        "id": "gpt-chat",
                        "model_picker_enabled": True,
                        "supported_endpoints": ["/chat/completions"],
                        "capabilities": {
                            "limits": {
                                "max_context_window_tokens": 400000,
                                "max_prompt_tokens": 272000,
                                "max_output_tokens": 128000,
                            }
                        },
                    },
                    {
                        "id": "gpt-responses",
                        "model_picker_enabled": True,
                        "supported_endpoints": ["/responses"],
                    },
                    {
                        "id": "hidden",
                        "model_picker_enabled": False,
                        "supported_endpoints": ["/chat/completions"],
                    },
                ]
            }

    monkeypatch.setattr(github_copilot_models, "apply_github_copilot_oauth_patch", lambda: None)
    monkeypatch.setattr(
        "litellm.llms.github_copilot.authenticator.Authenticator",
        lambda: FakeAuthenticator(),
    )
    monkeypatch.setattr(
        "litellm.llms.github_copilot.common_utils.get_copilot_default_headers",
        lambda api_key: {"Authorization": "Bearer api-key"},
    )
    monkeypatch.setattr(github_copilot_models.httpx, "get", lambda *_, **__: FakeResponse())

    assert github_copilot_models.fetch_available_models() == [
        {
            "name": "gpt-chat",
            "upstream": "github_copilot/gpt-chat",
            "max_tokens": 400000,
            "max_input_tokens": 272000,
            "max_output_tokens": 128000,
            "capabilities": {
                "limits": {
                    "max_context_window_tokens": 400000,
                    "max_prompt_tokens": 272000,
                    "max_output_tokens": 128000,
                }
            },
            "supported_endpoints": ["/chat/completions"],
        },
        {
            "name": "gpt-responses",
            "upstream": "github_copilot/gpt-responses",
            "mode": "responses",
            "supported_endpoints": ["/responses"],
        },
    ]


def test_refresh_and_load_model_cache(monkeypatch, tmp_path) -> None:
    registry = [
        {
            "name": "gpt-test",
            "upstream": "github_copilot/gpt-test",
            "max_tokens": 400000,
        }
    ]
    monkeypatch.setattr(github_copilot_models, "fetch_available_models", lambda: registry)
    cache = tmp_path / "models-cache.json"

    assert github_copilot_models.refresh_model_cache(cache) == registry
    assert github_copilot_models.load_model_cache(cache) == registry
    assert json.loads(cache.read_text(encoding="utf-8")) == {"models": registry}
