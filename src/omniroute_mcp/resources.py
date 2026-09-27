"""Layer 5 - MCP resources.

The second protocol primitive, often forgotten.

    tool     — ACTION. Called by the model when it decides to. Can change the world.
    resource — DATA. Read by the client/user via a URI. Read-only.

The difference is practical, not cosmetic: resources can be preloaded
into the context by the client (the user picks them in the UI, like a
file attachment) without spending a model turn on a tool call.

Rule of thumb: "the agent decides when it is needed" -> tool.
"This is reference context that might be needed upfront" -> resource.

Here we deliberately duplicate some of the data from tools.py — so that
in class both primitives can be shown on the same data.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import formatting
from .client import OmniRouteClient


def register_resources(mcp: FastMCP, client_factory) -> None:
    """Registers the OmniRoute resources.

    Args:
        mcp: the server instance.
        client_factory: a zero-argument function returning a live
            OmniRouteClient. Unlike tools, resources do not receive a
            Context, so the client is passed via a closure — the
            simplest form of dependency injection, and good enough here.
    """

    @mcp.resource("omniroute://health", mime_type="text/plain")
    async def health_resource() -> str:
        """Current status of the OmniRoute gateway (a snapshot at read time)."""
        client: OmniRouteClient = client_factory()
        return formatting.format_health(await client.health())

    @mcp.resource("omniroute://models", mime_type="text/plain")
    async def models_resource() -> str:
        """Catalog of models available through OmniRoute."""
        client: OmniRouteClient = client_factory()
        return formatting.format_models(await client.models())
