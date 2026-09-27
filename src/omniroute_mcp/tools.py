"""Layer 4 - MCP tools.

A tool = a function the agent may decide to call on its own.

The key FastMCP idea worth showing in class:
**a tool's schema is derived from the Python function's signature.**
Type annotations -> JSON Schema for parameters, docstring -> description
for the model. There is no separate schema file, and there does not
need to be one.

About the docstring: it is not read by a human, but by the LLM. It is
the only thing the model uses to decide whether to call the tool. So
the docstring here is part of the prompt, not documentation: it says
*when* to use the tool, not *how* it is implemented.

There are exactly 8 tools. OmniRoute's built-in MCP server exposes 104 —
and that is an anti-pattern for teaching: a large catalog bloats the
context and hurts selection accuracy. Better to have 8 tools the agent
will pick correctly.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from . import formatting
from .client import OmniRouteClient, OmniRouteError
from .config import Settings


def _client(ctx: Context) -> OmniRouteClient:
    """Fetches the shared HTTP client from the server's lifespan context.

    The client is created once at startup (see server.py) and reuses
    connections — opening a new one per tool call would be wasteful.
    """
    return ctx.request_context.lifespan_context.client


def _settings(ctx: Context) -> Settings:
    """Fetches the server settings from the lifespan context.

    Needed by tools that have a default coming from configuration —
    currently that is the model for omniroute_chat.
    """
    return ctx.request_context.lifespan_context.settings


def register_tools(mcp: FastMCP) -> None:
    """Registers all tools on the given server.

    Why a function instead of module-level decorators: this way the
    same set of tools can be attached to different server instances —
    for example, to a separate, "stripped-down" server used in tests.
    """

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    @mcp.tool()
    async def omniroute_health(ctx: Context) -> str:
        """Check the status of the OmniRoute gateway.

        Call this first if any other OmniRoute tool unexpectedly fails —
        it shows whether the gateway is alive, its version, uptime,
        circuit breaker states, and any problem providers.
        """
        return formatting.format_health(await _client(ctx).health())

    @mcp.tool()
    async def omniroute_list_models(ctx: Context, filter: str = "") -> str:
        """Show the models available through OmniRoute.

        Call this before omniroute_chat if you are unsure of the model
        name. Besides regular models it also returns auto/* pseudo-models
        (e.g. auto/cheap, auto/best-coding) — with those the gateway
        itself picks the provider.

        Args:
            filter: optional substring to filter by (e.g. "claude", "gpt").
        """
        return formatting.format_models(await _client(ctx).models(), filter)

    @mcp.tool()
    async def omniroute_list_providers(ctx: Context) -> str:
        """Show configured provider connections and their status.

        Useful for diagnostics: shows which providers are active, how
        they are authenticated, when their tokens expire, and what the
        last error was.
        """
        return formatting.format_providers(await _client(ctx).providers())

    @mcp.tool()
    async def omniroute_quota(ctx: Context) -> str:
        """Show remaining quota per provider and the status of their tokens.

        Call this if requests start failing due to limits, or when you
        need to pick a provider that still has quota headroom.
        """
        return formatting.format_quota(await _client(ctx).quota())

    @mcp.tool()
    async def omniroute_provider_stats(ctx: Context) -> str:
        """Show provider statistics: request volume, success rate, latency.

        Helps pick the fastest or most reliable provider based on
        actual measurements rather than guesswork.
        """
        return formatting.format_provider_stats(await _client(ctx).provider_stats())

    # ------------------------------------------------------------------
    # Traffic monitoring
    # ------------------------------------------------------------------

    @mcp.tool()
    async def omniroute_recent_calls(ctx: Context, limit: int = 10) -> str:
        """Show the most recent requests that went through the gateway.

        A self-diagnostic tool: shows where routing actually sent each
        request, how long it took, and whether there were any errors.

        Args:
            limit: how many recent calls to return (1..100).
        """
        limit = max(1, min(limit, 100))
        return formatting.format_call_logs(await _client(ctx).call_logs(limit))

    @mcp.tool()
    async def omniroute_usage_summary(ctx: Context, hours: int = 24) -> str:
        """Usage summary for the last N hours.

        Aggregates requests, tokens, errors, and a breakdown by model
        and provider. Use it for questions like "how much did I spend"
        or "what broke today".

        Args:
            hours: window size in hours (1..168).
        """
        hours = max(1, min(hours, 168))
        export = await _client(ctx).logs_export(hours)
        return formatting.format_usage_summary(export, hours)

    # ------------------------------------------------------------------
    # Inference — the server's main value
    # ------------------------------------------------------------------

    @mcp.tool()
    async def omniroute_chat(
        ctx: Context,
        prompt: str,
        model: str = "",
        system: str = "",
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> str:
        """Send a request to any LLM through OmniRoute's routing.

        Gives you access to dozens of models from different providers
        with automatic fallbacks. Use it to delegate a subtask to another
        model: for example, hand off a long, cheap summarization to a
        cheaper model, or ask a competing model for a second opinion.
        For available names, see omniroute_list_models.

        Args:
            prompt: the user message for the model.
            model: model name or an auto/* strategy. If omitted, the
                model from the server configuration (CLIENT_MODEL) is used.
            system: optional system prompt setting the role.
            max_tokens: response token limit. Do not go below ~512:
                for reasoning models, hidden reasoning eats into the
                budget and the answer can come back empty.
            temperature: optional sampling temperature (0.0-2.0).
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        data = await _client(ctx).chat_completion(
            model=model or _settings(ctx).require_model(),
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return formatting.format_chat_completion(data)


# ----------------------------------------------------------------------
# Error handling
# ----------------------------------------------------------------------
#
# Notice that none of the tools above are wrapped in try/except. That is
# deliberate. FastMCP catches the exception itself and returns it to the
# agent as a result with the isError flag set — the agent sees the error
# text and can react (e.g. call omniroute_health first).
#
# That is exactly why the OmniRouteError text in client.py is written
# for the model to read: "gateway unreachable, check the port" is more
# useful than a bare ConnectError.
#
# Here we simply record this as the module's public contract.
__all__ = ["register_tools", "OmniRouteError"]
