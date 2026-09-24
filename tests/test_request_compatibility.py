import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_model_context import catalog, model
from test_model_context import configured as configured

from main import app
from request_compatibility import CompatibilityError, normalize_request


def reasoner(efforts=None, vision=True):
    return {
        **model(),
        "supported_endpoints": ["/responses", "/chat/completions"],
        "capabilities": {
            "supports": {
                "vision": vision,
                "reasoning_effort": ["low", "medium", "high"] if efforts is None else efforts,
            }
        },
    }


@pytest.mark.parametrize("endpoint", ["/responses", "/chat/completions"])
@pytest.mark.parametrize("effort", ["none", "max", "high", None])
def test_old_session_effort_is_repaired_without_changing_content(endpoint, effort, caplog):
    body = {
        "model": "github_copilot/model-a",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_image", "image_url": "data:image/png;base64,x"}],
            },
            {"type": "function_call", "call_id": "call-1", "name": "lookup", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call-1", "output": "retained"},
        ],
        "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        "previous_response_id": "resp-old",
        "max_output_tokens": 128000,
        "metadata": {"example": "retained"},
    }
    if endpoint == "/responses":
        body["reasoning"] = {"summary": "auto", **({"effort": effort} if effort else {})}
    elif effort:
        body["reasoning_effort"] = effort
    before = copy.deepcopy(body)
    headers = normalize_request(body, [reasoner()], endpoint)
    expected = "high" if effort == "high" else "medium"
    if endpoint == "/responses":
        assert body["reasoning"] == {**before["reasoning"], "effort": expected}
    else:
        assert body["reasoning_effort"] == expected
    for key in before.keys() - {"reasoning", "reasoning_effort"}:
        assert body[key] == before[key]
    if effort in ("none", "max"):
        assert headers == {"X-VELA-Reasoning-Effort": "medium"}
        assert "Adjusted unsupported reasoning effort" in caplog.text
    else:
        assert headers == {}
        assert not caplog.text


def test_nonreasoning_unknown_and_supported_none_are_distinct():
    for entry, expected in [(reasoner([]), None), (model(), "max"), (reasoner(["none"]), "none")]:
        body = {"model": "model-a", "reasoning_effort": "max"}
        normalize_request(body, [entry], "/chat/completions")
        assert body.get("reasoning_effort") == expected


def test_incompatible_old_image_history_is_not_silently_dropped():
    body = {
        "model": "model-a",
        "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "old-image"}]}],
    }
    before = copy.deepcopy(body)
    with pytest.raises(CompatibilityError, match="image-capable model"):
        normalize_request(body, [reasoner(vision=False)], "/responses")
    assert body == before


def test_unsupported_protocol_has_actionable_error():
    entry = {**reasoner(), "supported_endpoints": ["/chat/completions"]}
    with pytest.raises(CompatibilityError, match="does not support /responses"):
        normalize_request({"model": "model-a"}, [entry], "/responses")


@pytest.mark.parametrize("value", [True, {}, 5])
def test_invalid_effort_shape_is_not_coerced(value):
    with pytest.raises(CompatibilityError, match="must be a string"):
        normalize_request(
            {"model": "model-a", "reasoning_effort": value}, [reasoner()], "/chat/completions"
        )


@pytest.mark.parametrize("route", ["responses", "chat/completions"])
@pytest.mark.parametrize("stream", [False, True])
def test_proxy_keeps_images_tools_and_multiturn_and_repairs_effort(
    configured, monkeypatch, route, stream
):
    _, config = configured
    catalog(config.parent, [reasoner()])
    calls = []
    is_responses = route == "responses"

    async def events():
        if is_responses:
            yield {"type": "response.output_text.delta", "delta": "OK"}
            yield {
                "type": "response.completed",
                "response": {"id": "resp-next", "status": "completed"},
            }
        else:
            yield {"choices": [{"delta": {"content": "OK"}}]}

    async def invoke(**body):
        calls.append(copy.deepcopy(body))
        if stream:
            return events()
        return {"id": "resp-next", "output": []} if is_responses else {"choices": []}

    monkeypatch.setattr(
        "main._litellm", lambda: SimpleNamespace(aresponses=invoke, acompletion=invoke)
    )
    image = (
        {"type": "input_image", "image_url": "data:image/png;base64,x"}
        if is_responses
        else {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}
    )
    history = [{"role": "user", "content": [image]}]
    body = {
        "model": "model-a",
        "stream": stream,
        "input" if is_responses else "messages": history,
        "tools": [{"type": "function", "name": "lookup"}],
        "reasoning" if is_responses else "reasoning_effort": {"effort": "max"}
        if is_responses
        else "max",
    }
    if is_responses:
        body["previous_response_id"] = "resp-old"
    response = TestClient(app).post(
        "/v1/" + route, headers={"Authorization": "Bearer test-key"}, json=body
    )
    assert response.status_code == 200
    assert response.headers["X-VELA-Reasoning-Effort"] == "medium"
    sent = calls[0]
    assert sent["input" if is_responses else "messages"] == history
    assert sent["tools"] == body["tools"]
    assert sent["model"] == "github_copilot/model-a"
    if is_responses:
        assert sent["reasoning"]["effort"] == "medium"
        assert sent["previous_response_id"] == "resp-old"
    else:
        assert sent["reasoning_effort"] == "medium"
    if stream:
        assert "OK" in response.text
        assert ("response.completed" if is_responses else "[DONE]") in response.text


@pytest.mark.parametrize("route", ["responses", "chat/completions"])
def test_midstream_failure_is_error_not_success(configured, monkeypatch, route):
    async def broken():
        yield {"type": "response.created", "id": "resp-1"}
        raise CompatibilityError("Upstream stream interrupted")

    async def invoke(**body):
        return broken()

    monkeypatch.setattr(
        "main._litellm", lambda: SimpleNamespace(aresponses=invoke, acompletion=invoke)
    )
    response = TestClient(app).post(
        "/v1/" + route,
        headers={"Authorization": "Bearer test-key"},
        json={"model": "model-a", "stream": True},
    )
    assert "Upstream stream interrupted" in response.text
    assert "response.completed" not in response.text
    assert "[DONE]" not in response.text
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert events[-1].get("type") == "error" or "error" in events[-1]
