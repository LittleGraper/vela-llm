from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from settings import get_settings


class FailingLiteLLM:
    async def acompletion(self, **_: object) -> None:
        raise RuntimeError("upstream exploded in C:\\secret\\.venv\\path")


def test_openai_route_shapes_upstream_errors(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "sk-test")
    get_settings.cache_clear()
    monkeypatch.setattr("main._litellm", lambda: FailingLiteLLM())

    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "message": "Upstream provider request failed.",
            "type": "upstream_error",
            "code": None,
        }
    }


def test_anthropic_route_shapes_upstream_errors(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "sk-test")
    get_settings.cache_clear()
    monkeypatch.setattr("main._litellm", lambda: FailingLiteLLM())

    client = TestClient(app)
    response = client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer sk-test"},
        json={"model": "gpt-4", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 502
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "api_error",
            "message": "Upstream provider request failed.",
        },
    }


def test_models_route_uses_model_registry(monkeypatch, tmp_path) -> None:
    config = tmp_path / "models.toml"
    config.write_text(
        """
[models]
default = "local-one"

[[models.aliases]]
name = "local-one"
upstream = "github_copilot/upstream-one"

[[models.aliases]]
name = "local-two"
upstream = "github_copilot/upstream-two"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("LOCAL_API_KEY", "sk-test")
    monkeypatch.setenv("VELA_LLM_MODELS_CONFIG", str(config))
    monkeypatch.setenv("VELA_LLM_DISABLE_DYNAMIC_MODELS", "1")
    monkeypatch.setenv("VELA_LLM_DEFAULT_MODEL", "")
    monkeypatch.setenv("VELA_LLM_MODEL_ALIASES", "")
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.get("/v1/models", headers={"Authorization": "Bearer sk-test"})

    assert response.status_code == 200
    assert [model["id"] for model in response.json()["data"]] == ["local-one"]
