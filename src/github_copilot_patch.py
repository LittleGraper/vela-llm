from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from github_copilot_headers import VELA_USER_AGENT, apply_github_copilot_header_patch


def apply_github_copilot_oauth_patch() -> None:
    apply_github_copilot_header_patch()
    from litellm._logging import verbose_logger
    from litellm.llms.github_copilot import authenticator as auth_module
    from litellm.llms.github_copilot.common_utils import (
        GetAccessTokenError,
        GetDeviceCodeError,
    )

    authenticator = auth_module.Authenticator
    if getattr(authenticator, "_vela_llm_oauth_patch", False):
        return

    def form_headers(self: Any) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": VELA_USER_AGENT,
        }

    def patched_get_device_code(self: Any) -> dict[str, str]:
        try:
            device_code_url = os.getenv(
                "GITHUB_COPILOT_DEVICE_CODE_URL", auth_module.DEFAULT_GITHUB_DEVICE_CODE_URL
            )
            client_id = os.getenv("GITHUB_COPILOT_CLIENT_ID", auth_module.DEFAULT_GITHUB_CLIENT_ID)
            with httpx.Client(timeout=30) as sync_client:
                response = sync_client.post(
                    device_code_url,
                    headers=form_headers(self),
                    data={"client_id": client_id, "scope": "read:user"},
                )
            response.raise_for_status()
            response_json = response.json()
            required_fields = ["device_code", "user_code", "verification_uri"]
            if not all(field in response_json for field in required_fields):
                verbose_logger.error(f"Response missing required fields: {response_json}")
                raise GetDeviceCodeError(
                    message="Response missing required fields",
                    status_code=400,
                )
            return response_json
        except httpx.HTTPStatusError as exc:
            verbose_logger.error(f"HTTP error getting device code: {exc}")
            raise GetDeviceCodeError(
                message=f"Failed to get device code: {exc}",
                status_code=400,
            ) from exc
        except json.JSONDecodeError as exc:
            verbose_logger.error(f"Error decoding JSON response: {exc}")
            raise GetDeviceCodeError(
                message=f"Failed to decode device code response: {exc}",
                status_code=400,
            ) from exc
        except Exception as exc:
            verbose_logger.error(f"Unexpected error getting device code: {exc}")
            raise GetDeviceCodeError(
                message=f"Failed to get device code: {exc}",
                status_code=400,
            ) from exc

    def patched_poll_for_access_token(self: Any, device_code: str) -> str:
        access_token_url = os.getenv(
            "GITHUB_COPILOT_ACCESS_TOKEN_URL", auth_module.DEFAULT_GITHUB_ACCESS_TOKEN_URL
        )
        client_id = os.getenv("GITHUB_COPILOT_CLIENT_ID", auth_module.DEFAULT_GITHUB_CLIENT_ID)

        max_attempts = 120
        for attempt in range(max_attempts):
            try:
                with httpx.Client(timeout=30) as sync_client:
                    response = sync_client.post(
                        access_token_url,
                        headers=form_headers(self),
                        data={
                            "client_id": client_id,
                            "device_code": device_code,
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        },
                    )
                response.raise_for_status()
                response_json = response.json()
                if "access_token" in response_json:
                    verbose_logger.info("Authentication successful!")
                    return response_json["access_token"]
                if response_json.get("error") == "authorization_pending":
                    verbose_logger.debug(
                        f"Authorization pending (attempt {attempt + 1}/{max_attempts})"
                    )
                else:
                    verbose_logger.warning(f"Unexpected response: {response_json}")
            except httpx.HTTPStatusError as exc:
                verbose_logger.error(f"HTTP error polling for access token: {exc}")
                raise GetAccessTokenError(
                    message=f"Failed to get access token: {exc}",
                    status_code=400,
                ) from exc
            except json.JSONDecodeError as exc:
                verbose_logger.error(f"Error decoding access token response: {exc}")
                raise GetAccessTokenError(
                    message=f"Failed to decode access token response: {exc}",
                    status_code=400,
                ) from exc
            except Exception as exc:
                verbose_logger.error(f"Unexpected error polling for access token: {exc}")
                raise GetAccessTokenError(
                    message=f"Failed to get access token: {exc}",
                    status_code=400,
                ) from exc

            time.sleep(5)

        raise GetAccessTokenError(
            message="Timed out waiting for user to authorize the device",
            status_code=400,
        )

    authenticator._get_device_code = patched_get_device_code
    authenticator._poll_for_access_token = patched_poll_for_access_token
    authenticator._vela_llm_oauth_patch = True
