from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Iterable
from typing import Any

from serialization import as_dict, get_value


def to_litellm_completion_kwargs(request: dict[str, Any], default_model: str) -> dict[str, Any]:
    messages = _anthropic_messages_to_openai(request)
    kwargs: dict[str, Any] = {
        "model": request.get("model") or default_model,
        "messages": messages,
    }

    field_map = {
        "max_tokens": "max_tokens",
        "temperature": "temperature",
        "top_p": "top_p",
        "stream": "stream",
        "metadata": "metadata",
    }
    for source, target in field_map.items():
        if source in request:
            kwargs[target] = request[source]

    if request.get("stop_sequences"):
        kwargs["stop"] = request["stop_sequences"]

    if request.get("tools"):
        kwargs["tools"] = [_anthropic_tool_to_openai(tool) for tool in request["tools"]]

    if "tool_choice" in request:
        kwargs["tool_choice"] = _anthropic_tool_choice_to_openai(request["tool_choice"])

    if "thinking" in request:
        kwargs["thinking"] = request["thinking"]

    return kwargs


def to_anthropic_message(response: Any, requested_model: str) -> dict[str, Any]:
    response_dict = as_dict(response)
    choice = response_dict.get("choices", [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    tool_calls = message.get("tool_calls") or []
    blocks: list[dict[str, Any]] = []

    if content:
        blocks.append({"type": "text", "text": content})

    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get("id") or f"toolu_{uuid.uuid4().hex}",
                "name": function.get("name", "tool"),
                "input": _json_object(function.get("arguments")),
            }
        )

    usage = response_dict.get("usage") or {}
    return {
        "id": response_dict.get("id") or f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": response_dict.get("model") or requested_model,
        "content": blocks or [{"type": "text", "text": ""}],
        "stop_reason": _finish_reason_to_anthropic(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


async def litellm_stream_to_anthropic_events(
    stream: AsyncIterator[Any],
    requested_model: str,
) -> AsyncIterator[str]:
    message_id = f"msg_{uuid.uuid4().hex}"
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": requested_model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )
    yield _sse(
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    )

    async for chunk in stream:
        chunk_dict = as_dict(chunk)
        choice = (chunk_dict.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        text = delta.get("content")
        if text:
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                },
            )

    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 0},
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


def _anthropic_messages_to_openai(request: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = request.get("system")
    if system:
        messages.append({"role": "system", "content": _system_content(system)})

    for message in request.get("messages", []):
        role = message.get("role", "user")
        content = message.get("content", "")

        if role == "assistant" and isinstance(content, list):
            messages.append(_assistant_message_from_blocks(content))
            continue

        if role == "user" and isinstance(content, list):
            user_blocks = [block for block in content if block.get("type") != "tool_result"]
            tool_blocks = [block for block in content if block.get("type") == "tool_result"]
            if user_blocks:
                messages.append({"role": "user", "content": _content_blocks_to_openai(user_blocks)})
            for block in tool_blocks:
                messages.append(_tool_result_to_openai(block))
            continue

        messages.append({"role": role, "content": _content_to_openai(content)})

    return messages


def _system_content(system: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    if isinstance(system, str):
        return system
    return _content_blocks_to_openai(system)


def _content_to_openai(content: Any) -> Any:
    if isinstance(content, list):
        return _content_blocks_to_openai(content)
    return content


def _content_blocks_to_openai(blocks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            converted.append({"type": "text", "text": block.get("text", "")})
        elif block_type == "image":
            converted.append({"type": "image_url", "image_url": {"url": _source_to_url(block)}})
        elif block_type == "document":
            converted.append(_document_to_openai(block))
        elif block_type == "tool_result":
            converted.append({"type": "text", "text": _tool_result_text(block)})
        else:
            converted.append(block)
    return converted


def _assistant_message_from_blocks(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text" and block.get("text"):
            text_parts.append(block["text"])
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or f"toolu_{uuid.uuid4().hex}",
                    "type": "function",
                    "function": {
                        "name": block.get("name", "tool"),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )

    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _tool_result_to_openai(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": block.get("tool_use_id") or block.get("id") or "toolu_unknown",
        "content": _tool_result_text(block),
    }


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item))
        return "\n".join(part for part in parts if part)
    return json.dumps(content)


def _source_to_url(block: dict[str, Any]) -> str:
    source = block.get("source") or {}
    source_type = source.get("type")
    if source_type == "url":
        return source.get("url", "")
    if source_type == "base64":
        media_type = source.get("media_type", "application/octet-stream")
        return f"data:{media_type};base64,{source.get('data', '')}"
    return source.get("url") or source.get("data") or ""


def _document_to_openai(block: dict[str, Any]) -> dict[str, Any]:
    source = block.get("source") or {}
    if source.get("type") == "base64":
        media_type = source.get("media_type", "application/pdf")
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": source.get("data", ""),
            },
            "title": block.get("title") or block.get("name"),
        }
    return block


def _anthropic_tool_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema") or tool.get("parameters") or {"type": "object"},
        },
    }


def _anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str):
        return tool_choice
    choice_type = tool_choice.get("type")
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "none":
        return "none"
    if choice_type == "tool":
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    return tool_choice


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"arguments": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _finish_reason_to_anthropic(reason: str | None) -> str | None:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence",
    }.get(reason, reason)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def model_object(model_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    model = {
        "id": model_id,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "github-copilot",
    }
    if metadata:
        for key in ("max_tokens", "max_input_tokens", "max_output_tokens"):
            if key in metadata:
                model[key] = metadata[key]
    return model


def chunk_text(chunk: Any) -> str:
    chunk_dict = as_dict(chunk)
    choices = chunk_dict.get("choices") or []
    if not choices:
        return ""
    delta = get_value(choices[0], "delta", {})
    return get_value(delta, "content", "") or ""
