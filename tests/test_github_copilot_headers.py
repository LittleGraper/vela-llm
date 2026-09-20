from __future__ import annotations

import pytest

# Import providers before applying VELA's patch to exercise cached helper imports.
from litellm.llms.github_copilot.authenticator import Authenticator
from litellm.llms.github_copilot.chat.transformation import GithubCopilotConfig
from litellm.llms.github_copilot.embedding.transformation import GithubCopilotEmbeddingConfig
from litellm.llms.github_copilot.responses.transformation import GithubCopilotResponsesAPIConfig

from github_copilot_headers import apply_github_copilot_header_patch, github_user_headers


@pytest.mark.parametrize("provider", ["chat", "responses", "embedding"])
def test_provider_headers_use_current_profile(monkeypatch, tmp_path, provider) -> None:
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(Authenticator, "get_api_key", lambda self: "test-token")
    apply_github_copilot_header_patch()
    apply_github_copilot_header_patch()
    if provider == "responses":
        headers = GithubCopilotResponsesAPIConfig().validate_environment(
            {"x-custom": "keep"}, "gpt-6-astra", None
        )
    else:
        config = GithubCopilotConfig() if provider == "chat" else GithubCopilotEmbeddingConfig()
        headers = config.validate_environment(
            {"x-custom": "keep"}, "gpt-6-astra", [], {}, {}, api_key="test-token"
        )
    assert headers["x-github-api-version"] == "2026-08-01"
    assert headers["editor-version"] == "vscode/1.137.0"
    assert headers["editor-plugin-version"] == "copilot-chat/0.65.0"
    assert headers["user-agent"] == "GitHubCopilotChat/0.65.0"
    assert headers["x-custom"] == "keep"
    assert headers["Authorization"] == "Bearer test-token"


def test_token_exchange_and_public_rest_use_separate_headers(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(tmp_path))
    apply_github_copilot_header_patch()
    headers = Authenticator()._get_github_headers("test-token")
    assert headers["editor-version"] == "vscode/1.137.0"
    assert headers["editor-plugin-version"] == "copilot-chat/0.65.0"
    assert headers["authorization"] == "token test-token"
    assert "x-github-api-version" not in headers
    assert github_user_headers("test-token")["x-github-api-version"] == "2022-11-28"
