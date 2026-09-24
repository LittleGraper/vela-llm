import httpx
import pytest
from test_client_config import clients as clients
from test_client_updates import capable
from test_model_context import catalog
from test_model_context import configured as configured

import client_health
from client_health import check_client


def mock_transport(monkeypatch, handler):
    factory = httpx.Client
    monkeypatch.setattr(
        client_health.httpx,
        "Client",
        lambda **kwargs: factory(**kwargs, transport=httpx.MockTransport(handler)),
    )


def test_saved_config_is_not_reported_as_inference_passed(clients, monkeypatch):
    clients.apply(clients.plan("codex"))
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})

    mock_transport(monkeypatch, handler)
    check = check_client(clients, "codex")
    assert check.ok and "inference not tested" in check.message
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("status,expected", [(401, "Authentication"), (429, "quota"), (503, "503")])
def test_http_failures_are_explicit_and_redacted(clients, monkeypatch, status, expected):
    clients.apply(clients.plan("codex"))
    mock_transport(
        monkeypatch, lambda request: httpx.Response(status, text="private-token-in-server-error")
    )
    check = check_client(clients, "codex")
    assert not check.ok
    assert expected in check.message
    assert "private-token" not in check.message


def test_offline_proxy_does_not_undo_saved_configuration(clients, monkeypatch):
    clients.apply(clients.plan("codex"))
    before = [p.read_bytes() for p in clients.paths("codex")]

    def offline(request):
        raise httpx.ConnectError("private details")

    mock_transport(monkeypatch, offline)
    check = check_client(clients, "codex")
    assert not check.ok and "/start" in check.message
    assert [p.read_bytes() for p in clients.paths("codex")] == before


@pytest.mark.parametrize("complete", [True, False])
def test_text_probe_requires_completed_answer(clients, monkeypatch, complete):
    clients.apply(clients.plan("codex"))
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})
        return httpx.Response(
            200,
            json={
                "status": "completed" if complete else "incomplete",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
            },
        )

    mock_transport(monkeypatch, handler)
    check = check_client(clients, "codex", inference=True)
    assert check.ok is complete
    assert len(calls) == 2
    assert calls[-1].url.path == "/v1/responses"
    if complete:
        assert "not certified" in check.message


def test_wrong_proxy_catalog_is_not_success(clients, monkeypatch):
    clients.apply(clients.plan("codex"))
    mock_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"data": [{"id": "other"}]})
    )
    result = check_client(clients, "codex")
    assert not result.ok and "differs" in result.message


@pytest.mark.parametrize("client", ["dsh", "kimi"])
def test_probe_uses_actual_responses_provider_for_dual_protocol_models(
    clients, monkeypatch, client
):
    catalog(clients.settings.model_cache_path.parent, [capable()])
    clients.apply(clients.plan(client))
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "model-a"}]})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
            },
        )

    mock_transport(monkeypatch, handler)
    assert check_client(clients, client, inference=True).ok
    assert paths == ["/v1/models", "/v1/responses"]
