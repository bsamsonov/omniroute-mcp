"""Layer 2 - the OmniRoute HTTP client.

The only module that knows the gateway's URLs and API quirks. It knows
nothing about MCP — it can be reused in a plain script.

Lesson: keep "knowledge of the external service" in one place. When
OmniRoute changes a path, you fix one file, not eight tools.

All the paths below were verified against a live v3.8.42 instance — we
did not guess at the API, we recorded it from a running gateway.
"""

from __future__ import annotations

from typing import Any

import httpx


class OmniRouteError(RuntimeError):
    """An OmniRoute request error with a message the agent can understand.

    Important: the message is read by the LLM. It should suggest what
    to do next, not describe the exception's internals.
    """


class OmniRouteClient:
    """A thin wrapper around the OmniRoute REST API.

    A single instance lives for the server's whole lifetime and reuses
    TCP connections (see the lifespan in server.py).
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        headers = {"Content-Type": "application/json"}
        if api_key:
            # Bearer covers both CLIENT_API (/v1/*) and MANAGEMENT (/api/*),
            # as long as the key has the "manage" scope.
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Transport: the single place that handles network errors and statuses
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise OmniRouteError(
                f"Could not connect to OmniRoute at {self._base_url}. "
                "Check that the gateway is running and that OMNIROUTE_BASE_URL points to the right port."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OmniRouteError(
                f"OmniRoute did not respond in time to {method} {path}. "
                "Routing with fallbacks can be slow — increase OMNIROUTE_TIMEOUT."
            ) from exc

        if response.status_code == 401:
            raise OmniRouteError(
                "OmniRoute rejected the request: 401 Authentication required. "
                "Set OMNIROUTE_API_KEY to a key with the 'manage' scope "
                "(Dashboard -> API Keys -> enable Management Access)."
            )
        if response.status_code == 403:
            raise OmniRouteError(
                f"OmniRoute rejected the request: 403 Forbidden on {path}. "
                "The key lacks the scope required for this endpoint."
            )
        if response.status_code >= 400:
            raise OmniRouteError(
                f"OmniRoute returned HTTP {response.status_code} for {method} {path}: "
                f"{_extract_error(response)}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise OmniRouteError(
                f"OmniRoute returned a non-JSON response for {method} {path} "
                f"(content-type: {response.headers.get('content-type', 'unknown')})."
            ) from exc

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    # ------------------------------------------------------------------
    # Management API — requires the "manage" scope
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Gateway status: version, uptime, circuit breakers, provider health."""
        return await self._get("/api/monitoring/health")

    async def models(self) -> list[dict[str, Any]]:
        """Model catalog in the namespace accepted by /v1/chat/completions.

        Why `/v1/models` and not the management endpoint `/api/models`:
        they return DIFFERENT sets, verified against the live gateway.

            /api/models -> 30 models, prefixes shortened (`cl/`, `kr/`, `oc/`),
                            no `auto/*` pseudo-models at all;
            /v1/models  -> 125 models, including `auto/cheap`, `auto/best-coding`.

        The tool needs exactly the second list: the agent sees a model
        name and passes it straight to `omniroute_chat`. Showing a
        catalog from a different namespace would hand the agent names
        that inference will not accept.

        Lesson: the list of "what can be called" must come from the same
        API surface as the call itself.
        """
        data = await self._get("/v1/models")
        return data.get("data", [])

    async def providers(self) -> list[dict[str, Any]]:
        """Provider connections (called "connections" in the OmniRoute API)."""
        data = await self._get("/api/providers")
        return data.get("connections", [])

    async def quota(self) -> list[dict[str, Any]]:
        """Remaining quota per provider and the status of their tokens."""
        data = await self._get("/api/usage/quota")
        return data.get("providers", [])

    async def provider_stats(self) -> list[dict[str, Any]]:
        """Aggregated stats: request counts, success rate, average latency."""
        data = await self._get("/api/provider-stats")
        return data.get("providers", [])

    async def call_logs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Most recent calls that went through the gateway (newest first)."""
        data = await self._get("/api/usage/call-logs", params={"limit": limit})
        # The endpoint returns a bare array, but guard against a shape change.
        return data if isinstance(data, list) else data.get("logs", [])

    async def logs_export(self, hours: int = 24) -> dict[str, Any]:
        """Export of call logs for the last N hours (1..168)."""
        return await self._get(
            "/api/logs/export",
            params={"hours": hours, "type": "call-logs"},
        )

    # ------------------------------------------------------------------
    # Client API — OpenAI-compatible surface
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Runs a request through OmniRoute's routing.

        Two quirks of the live gateway are why explicit values are set here:

        1. `stream: false` is mandatory. By default a key works in
           streamDefaultMode="legacy" mode, and the gateway responds with
           text/event-stream, which `response.json()` cannot parse.
        2. `max_tokens` must have headroom: for reasoning models the
           budget is eaten by hidden reasoning, and with a small limit
           the gateway returns 502 "reasoning consumed N/N tokens — no
           content output".

        Args:
            tools: a list of functions in OpenAI format. If given, the
                model may return `tool_calls` instead of text — this is
                the basis of the autonomous agent loop (see omniroute_agent/core.py).
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
        return await self._request("POST", "/v1/chat/completions", json=payload)


def _extract_error(response: httpx.Response) -> str:
    """Pulls a human-readable message out of an OmniRoute error envelope.

    Format: {"error": {"code": "...", "message": "...", "correlation_id": "..."}}
    But on 5xx it can also be HTML — in that case truncate it so it does
    not clutter the context.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:200].strip() or "<empty response>"

    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message", error))
    if isinstance(error, str):
        return error
    return str(body)[:200]
