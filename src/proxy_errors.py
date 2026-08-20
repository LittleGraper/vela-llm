from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def openai_error_response(exc: Exception, status_code: int | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=_status_code(exc, status_code),
        content={
            "error": {
                "message": _safe_message(exc),
                "type": _error_type(exc),
                "code": _error_code(exc),
            }
        },
    )


def anthropic_error_response(exc: Exception, status_code: int | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=_status_code(exc, status_code),
        content={
            "type": "error",
            "error": {
                "type": _anthropic_error_type(exc),
                "message": _safe_message(exc),
            },
        },
    )


def _status_code(exc: Exception, fallback: int | None) -> int:
    status = fallback or _get_attr(exc, "status_code") or _get_attr(exc, "http_status")
    if isinstance(status, int) and 400 <= status < 600:
        return status
    return 502


def _safe_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    for marker in ("Traceback", "site-packages", "\\.venv", "/.venv"):
        if marker in message:
            return "Upstream provider request failed."
    return message


def _error_type(exc: Exception) -> str:
    status = _status_code(exc, None)
    if status in {401, 403}:
        return "authentication_error"
    if status == 429:
        return "rate_limit_error"
    if 400 <= status < 500:
        return "invalid_request_error"
    return "upstream_error"


def _anthropic_error_type(exc: Exception) -> str:
    status = _status_code(exc, None)
    if status in {401, 403}:
        return "authentication_error"
    if status == 429:
        return "rate_limit_error"
    if 400 <= status < 500:
        return "invalid_request_error"
    return "api_error"


def _error_code(exc: Exception) -> str | None:
    code = _get_attr(exc, "code") or _get_attr(exc, "error_code")
    return code if isinstance(code, str) else None


def _get_attr(exc: Exception, name: str) -> Any:
    value = getattr(exc, name, None)
    if value is not None:
        return value
    response = getattr(exc, "response", None)
    return getattr(response, name, None)
