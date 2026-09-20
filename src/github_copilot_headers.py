"""Shared request versions for VELA's Copilot compatibility layer."""

from __future__ import annotations

from importlib import import_module

# Copilot and the public GitHub REST API use separate version schemes.
COPILOT_API_VERSION = "2026-08-01"
GITHUB_REST_API_VERSION = "2022-11-28"
EDITOR_VERSION = "vscode/1.137.0"
PLUGIN_VERSION = "copilot-chat/0.65.0"
COPILOT_USER_AGENT = "GitHubCopilotChat/0.65.0"
VELA_USER_AGENT = "vela-llm"


def github_user_headers(access_token: str) -> dict[str, str]:
    return {
        "accept": "application/vnd.github+json",
        "authorization": f"token {access_token}",
        "user-agent": VELA_USER_AGENT,
        "x-github-api-version": GITHUB_REST_API_VERSION,
    }


def apply_github_copilot_header_patch() -> None:
    from litellm.llms.github_copilot import authenticator, common_utils

    original = common_utils.get_copilot_default_headers
    if getattr(original, "_vela_headers", False):
        return

    def current_headers(api_key: str) -> dict[str, str]:
        return {
            **original(api_key),
            "x-github-api-version": COPILOT_API_VERSION,
            "editor-version": EDITOR_VERSION,
            "editor-plugin-version": PLUGIN_VERSION,
            "user-agent": COPILOT_USER_AGENT,
        }

    current_headers._vela_headers = True
    common_utils.get_copilot_default_headers = current_headers
    # LiteLLM imports the helper by value. Update already-loaded providers too.
    for provider in ("chat", "responses", "embedding"):
        module = import_module(f"litellm.llms.github_copilot.{provider}.transformation")
        module.get_copilot_default_headers = current_headers

    def token_headers(self, access_token: str | None = None) -> dict[str, str]:
        headers = {
            "accept": "application/json",
            "editor-version": EDITOR_VERSION,
            "editor-plugin-version": PLUGIN_VERSION,
            "user-agent": COPILOT_USER_AGENT,
            "accept-encoding": "gzip,deflate,br",
        }
        if access_token:
            headers["authorization"] = f"token {access_token}"
        return headers

    authenticator.Authenticator._get_github_headers = token_headers
