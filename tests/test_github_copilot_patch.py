from __future__ import annotations

from typing import Any

import httpx

from github_copilot_patch import apply_github_copilot_oauth_patch


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeClient:
    posts: list[dict[str, Any]] = []

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, url: str, headers: dict[str, str], data: dict[str, str]) -> FakeResponse:
        self.posts.append({"url": url, "headers": headers, "data": data, "timeout": self.timeout})
        if url.endswith("/device/code"):
            return FakeResponse(
                {
                    "device_code": "device-code",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                }
            )
        return FakeResponse({"access_token": "access-token"})


def test_patch_uses_form_encoded_standalone_client(monkeypatch) -> None:
    from litellm.llms.github_copilot import authenticator as auth_module

    monkeypatch.delattr(auth_module.Authenticator, "_vela_llm_oauth_patch", raising=False)
    monkeypatch.setattr(httpx, "Client", FakeClient)
    FakeClient.posts = []

    apply_github_copilot_oauth_patch()

    authenticator = auth_module.Authenticator()
    device = authenticator._get_device_code()
    token = authenticator._poll_for_access_token(device["device_code"])

    assert token == "access-token"
    assert FakeClient.posts[0]["data"] == {
        "client_id": auth_module.DEFAULT_GITHUB_CLIENT_ID,
        "scope": "read:user",
    }
    assert "json" not in FakeClient.posts[0]
    content_type = FakeClient.posts[0]["headers"]["content-type"]
    assert content_type == "application/x-www-form-urlencoded"
    assert FakeClient.posts[1]["data"]["device_code"] == "device-code"
