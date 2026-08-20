from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from settings import get_settings


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _auth_error(path: str) -> dict[str, object]:
    if path.endswith("/messages") or "/messages" in path:
        return {
            "type": "error",
            "error": {
                "type": "authentication_error",
                "message": "Invalid or missing API key.",
            },
        }
    return {
        "error": {
            "message": "Invalid or missing API key.",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }


async def require_api_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    settings = get_settings()
    if not settings.local_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LOCAL_API_KEY is not configured.",
        )

    token = _bearer_token(authorization)
    if token and secrets.compare_digest(token, settings.local_api_key):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_auth_error(request.url.path),
        headers={"WWW-Authenticate": "Bearer"},
    )
