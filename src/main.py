from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from anthropic_transform import (
    litellm_stream_to_anthropic_events,
    model_object,
    to_anthropic_message,
    to_litellm_completion_kwargs,
)
from auth import require_api_key
from proxy_errors import anthropic_error_response, openai_error_response
from serialization import as_dict
from settings import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().validate_runtime()
    yield


try:
    APP_VERSION = version("vela-llm")
except PackageNotFoundError:
    APP_VERSION = "unknown"


app = FastAPI(title="vela-llm", version=APP_VERSION, lifespan=lifespan)


@app.exception_handler(HTTPException)
async def shaped_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and (
        "error" in exc.detail or exc.detail.get("type") == "error"
    ):
        return JSONResponse(exc.detail, status_code=exc.status_code, headers=exc.headers)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.get("/healthz")
@app.get("/v1/healthz")
async def healthz() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "default_model": settings.default_model,
        "models": settings.aliases,
    }


@app.get("/v1/models", dependencies=[Depends(require_api_key)])
@app.get("/models", dependencies=[Depends(require_api_key)])
async def models() -> dict[str, Any]:
    registry = get_settings().model_registry()
    return {
        "object": "list",
        "data": [model_object(model["name"], model) for model in registry],
    }


@app.post("/v1/chat/completions", dependencies=[Depends(require_api_key)], response_model=None)
@app.post("/chat/completions", dependencies=[Depends(require_api_key)], response_model=None)
async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    settings = get_settings()
    body["model"] = settings.upstream_model(body.get("model"))
    litellm = _litellm()

    try:
        if body.get("stream"):
            stream = await litellm.acompletion(**body)
            return StreamingResponse(_openai_sse(stream), media_type="text/event-stream")

        response = await litellm.acompletion(**body)
        return JSONResponse(as_dict(response))
    except Exception as exc:
        return openai_error_response(exc)


@app.post("/v1/messages", dependencies=[Depends(require_api_key)], response_model=None)
@app.post("/messages", dependencies=[Depends(require_api_key)], response_model=None)
async def anthropic_messages(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    settings = get_settings()
    requested_model = body.get("model") or settings.default_model
    kwargs = to_litellm_completion_kwargs(body, requested_model)
    kwargs["model"] = settings.upstream_model(kwargs.get("model"))
    litellm = _litellm()

    try:
        if kwargs.get("stream"):
            stream = await litellm.acompletion(**kwargs)
            return StreamingResponse(
                litellm_stream_to_anthropic_events(stream, requested_model),
                media_type="text/event-stream",
            )

        response = await litellm.acompletion(**kwargs)
        return JSONResponse(to_anthropic_message(response, requested_model))
    except Exception as exc:
        return anthropic_error_response(exc)


@app.post("/v1/embeddings", dependencies=[Depends(require_api_key)])
@app.post("/embeddings", dependencies=[Depends(require_api_key)])
async def embeddings(request: Request) -> JSONResponse:
    body = await request.json()
    body["model"] = get_settings().upstream_model(body.get("model"))
    litellm = _litellm()
    try:
        response = await litellm.aembedding(**body)
        return JSONResponse(as_dict(response))
    except Exception as exc:
        return openai_error_response(exc)


@app.post("/v1/responses", dependencies=[Depends(require_api_key)], response_model=None)
async def responses(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    body["model"] = get_settings().upstream_model(body.get("model"))
    litellm = _litellm()

    try:
        if body.get("stream"):
            stream = await litellm.aresponses(**body)
            return StreamingResponse(_openai_response_sse(stream), media_type="text/event-stream")

        response = await litellm.aresponses(**body)
        return JSONResponse(as_dict(response))
    except Exception as exc:
        return openai_error_response(exc)


async def _openai_sse(stream: AsyncIterator[Any]) -> AsyncIterator[str]:
    async for chunk in stream:
        yield f"data: {json.dumps(as_dict(chunk), separators=(',', ':'))}\n\n"
    yield "data: [DONE]\n\n"


async def _openai_response_sse(stream: AsyncIterator[Any]) -> AsyncIterator[str]:
    async for event in stream:
        event_dict = as_dict(event)
        event_name = event_dict.get("type", "message")
        yield f"event: {event_name}\ndata: {json.dumps(event_dict, separators=(',', ':'))}\n\n"


def _litellm() -> Any:
    import litellm

    from github_copilot_patch import apply_github_copilot_oauth_patch
    from litellm_registry import register_litellm_model_metadata

    apply_github_copilot_oauth_patch()
    register_litellm_model_metadata(litellm, get_settings().model_registry())
    return litellm


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    run()
