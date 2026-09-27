"""Tests for the HTTP client — using mocks, without a running OmniRoute.

Lesson: an MCP server is a plain program, and it should be tested like
one. Tying tests to a live gateway makes them slow and brittle: you
cannot reproduce a 401, a timeout, or a dropped connection on demand.
`respx` replaces httpx's transport and lets us test exactly our own code.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from omniroute_mcp.client import OmniRouteClient, OmniRouteError

BASE = "http://localhost:20128"


def _sent_body(route: Any) -> dict[str, Any]:
    """Body of the last request sent to this route, as a dict."""
    return json.loads(route.calls.last.request.read().decode())


@pytest.fixture
async def client():
    c = OmniRouteClient(BASE, "sk-test-key", timeout=5)
    yield c
    await c.aclose()


@respx.mock
async def test_bearer_header_is_sent(client: OmniRouteClient) -> None:
    """The key must be sent as Bearer — otherwise management endpoints return 401."""
    route = respx.get(f"{BASE}/api/monitoring/health").mock(
        return_value=httpx.Response(200, json={"status": "healthy"})
    )
    await client.health()
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-test-key"


@respx.mock
async def test_models_reads_v1_namespace(client: OmniRouteClient) -> None:
    """The catalog must come from /v1/models — the same namespace as inference."""
    route = respx.get(f"{BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "auto/cheap"}]})
    )
    models = await client.models()
    assert route.called
    assert models == [{"id": "auto/cheap"}]


@respx.mock
async def test_chat_forces_stream_false(client: OmniRouteClient) -> None:
    """Regression test for a real gateway trap.

    Without stream=false, OmniRoute responds with text/event-stream, and JSON parsing fails.
    """
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    await client.chat_completion("auto/cheap", [{"role": "user", "content": "hi"}])
    # Parse the body as JSON rather than searching for a substring: whitespace
    # in serialization is an httpx implementation detail we should not rely on.
    assert _sent_body(route)["stream"] is False


@respx.mock
async def test_chat_omits_temperature_when_none(client: OmniRouteClient) -> None:
    """None must not be sent to the server: models have different default temperatures."""
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    await client.chat_completion("auto/cheap", [{"role": "user", "content": "hi"}])
    assert "temperature" not in _sent_body(route)


@respx.mock
async def test_chat_passes_tools_when_given(client: OmniRouteClient) -> None:
    """Without passing tools through, the autonomous agent loop would not work."""
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    tools = [{"type": "function", "function": {"name": "omniroute_health"}}]
    await client.chat_completion("auto/cheap", [{"role": "user", "content": "hi"}], tools=tools)
    assert _sent_body(route)["tools"] == tools


@respx.mock
async def test_chat_omits_tools_when_empty(client: OmniRouteClient) -> None:
    """An empty list must not be sent: some providers treat that as an error."""
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    await client.chat_completion("auto/cheap", [{"role": "user", "content": "hi"}], tools=[])
    assert "tools" not in _sent_body(route)


@respx.mock
async def test_401_explains_manage_scope(client: OmniRouteClient) -> None:
    """The error message is written for the model: it should point to a way out."""
    respx.get(f"{BASE}/api/providers").mock(
        return_value=httpx.Response(401, json={"error": {"message": "Authentication required"}})
    )
    with pytest.raises(OmniRouteError, match="manage"):
        await client.providers()


@respx.mock
async def test_connect_error_names_the_url(client: OmniRouteClient) -> None:
    """If the gateway is not running, the agent should see the address, not a ConnectError."""
    respx.get(f"{BASE}/api/monitoring/health").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(OmniRouteError, match=BASE):
        await client.health()


@respx.mock
async def test_upstream_error_message_is_extracted(client: OmniRouteClient) -> None:
    """The OmniRoute error envelope must be unwrapped, not passed through as-is."""
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(
            502, json={"error": {"message": "reasoning consumed 19/20 tokens"}}
        )
    )
    with pytest.raises(OmniRouteError, match="reasoning consumed"):
        await client.chat_completion("auto/cheap", [{"role": "user", "content": "hi"}])


@respx.mock
async def test_html_error_is_truncated(client: OmniRouteClient) -> None:
    """On unknown paths, Next.js returns ~460 KB of HTML. It must not end up in the context."""
    respx.get(f"{BASE}/api/usage/quota").mock(
        return_value=httpx.Response(404, text="<!DOCTYPE html>" + "x" * 500_000)
    )
    with pytest.raises(OmniRouteError) as exc:
        await client.quota()
    assert len(str(exc.value)) < 500


@respx.mock
async def test_call_logs_accepts_bare_array(client: OmniRouteClient) -> None:
    """The endpoint returns a bare array — the client must not expect a wrapper."""
    respx.get(f"{BASE}/api/usage/call-logs").mock(
        return_value=httpx.Response(200, json=[{"id": "1", "status": 200}])
    )
    assert (await client.call_logs(1))[0]["id"] == "1"
