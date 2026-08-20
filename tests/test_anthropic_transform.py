import json

import pytest

from anthropic_transform import (
    litellm_stream_to_anthropic_events,
    to_anthropic_message,
    to_litellm_completion_kwargs,
)


def test_translates_text_system_tools_and_stop_sequences() -> None:
    request = {
        "model": "gpt-4",
        "system": "You are concise.",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 100,
        "stop_sequences": ["END"],
        "tools": [
            {
                "name": "lookup",
                "description": "Look up a value",
                "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
            }
        ],
        "tool_choice": {"type": "tool", "name": "lookup"},
    }

    kwargs = to_litellm_completion_kwargs(request, "fallback")

    assert kwargs["model"] == "gpt-4"
    assert kwargs["messages"][0] == {"role": "system", "content": "You are concise."}
    assert kwargs["messages"][1] == {"role": "user", "content": "Hello"}
    assert kwargs["max_tokens"] == 100
    assert kwargs["stop"] == ["END"]
    assert kwargs["tools"][0]["function"]["name"] == "lookup"
    assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "lookup"}}


def test_translates_image_document_and_tool_result_blocks() -> None:
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe these."},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
                    },
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": "def",
                        },
                        "title": "spec.pdf",
                    },
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "42"},
                ],
            }
        ]
    }

    kwargs = to_litellm_completion_kwargs(request, "gpt-4o")

    user_message = kwargs["messages"][0]
    tool_message = kwargs["messages"][1]

    assert user_message["role"] == "user"
    assert user_message["content"][1]["image_url"]["url"] == "data:image/png;base64,abc"
    assert user_message["content"][2]["source"]["data"] == "def"
    assert tool_message == {"role": "tool", "tool_call_id": "toolu_1", "content": "42"}


def test_translates_litellm_tool_call_response_to_anthropic() -> None:
    response = {
        "id": "chatcmpl_1",
        "model": "gpt-4",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"id":"a"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }

    message = to_anthropic_message(response, "gpt-4")

    assert message["stop_reason"] == "tool_use"
    assert message["usage"] == {"input_tokens": 3, "output_tokens": 4}
    assert message["content"][0] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "lookup",
        "input": {"id": "a"},
    }


def test_translates_assistant_tool_use_blocks_to_openai_tool_calls() -> None:
    request = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will look that up."},
                    {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"id": "a"}},
                ],
            }
        ]
    }

    kwargs = to_litellm_completion_kwargs(request, "gpt-4")
    message = kwargs["messages"][0]

    assert message["role"] == "assistant"
    assert message["content"] == "I will look that up."
    assert message["tool_calls"] == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"id": "a"}'},
        }
    ]


@pytest.mark.asyncio
async def test_streaming_chunks_are_mapped_to_anthropic_sse_events() -> None:
    async def stream():
        yield {"choices": [{"delta": {"content": "hello"}}]}
        yield {"choices": [{"delta": {"content": " world"}}]}

    events = [event async for event in litellm_stream_to_anthropic_events(stream(), "gpt-4")]

    assert events[0].startswith("event: message_start")
    assert events[1].startswith("event: content_block_start")
    assert events[-1].startswith("event: message_stop")

    delta_payloads = [
        json.loads(event.split("data: ", 1)[1])
        for event in events
        if event.startswith("event: content_block_delta")
    ]
    assert [payload["delta"]["text"] for payload in delta_payloads] == ["hello", " world"]
