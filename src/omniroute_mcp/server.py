"""Layer 7 - server assembly and transport selection.

Everything comes together here: settings -> client -> MCP primitives -> transport.

About transports (an important point for the lecture). Both are supported:

    stdio           — the server is launched by the agent as a CHILD
                      PROCESS, JSON-RPC is exchanged over stdin/stdout.
                      No port, no network. This is how most local agents
                      work (Claude Code, Claude Desktop, Cursor). Default.

    streamable-http — the server listens on an HTTP port. Needed when
                      the agent lives in a different container or on a
                      different machine and cannot spawn a subprocess.

A consequence for stdio that everyone trips over: **stdout belongs to
the protocol.** A single stray `print()` to stdout breaks the JSON-RPC
handshake. That is why all diagnostics go to stderr (see `log()`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from .client import OmniRouteClient, OmniRouteError
from .config import ConfigError, Settings
from .prompts import register_prompts
from .resources import register_resources
from .tools import register_tools

INSTRUCTIONS = """\
Access to OmniRoute — a local gateway to LLMs that combines multiple
providers with automatic routing and fallbacks.

The tools let you: send a request to any model (omniroute_chat), view
the catalog of models and providers, check quotas and gateway status,
and inspect recent traffic.

If an OmniRoute tool returns an error, start with omniroute_health to
see whether the gateway is reachable.\
"""


def log(message: str) -> None:
    """Diagnostics strictly go to stderr: stdout belongs to the MCP protocol."""
    print(f"[omniroute-mcp] {message}", file=sys.stderr, flush=True)


@dataclass
class AppContext:
    """Objects that live as long as the server does."""

    client: OmniRouteClient
    settings: Settings


def create_server(settings: Settings | None = None) -> FastMCP:
    """Assembles a ready-to-run MCP server.

    Extracted into a function so the same server can be assembled in
    tests with swapped-in settings, without actually starting anything.
    """
    settings = settings or Settings.from_env()

    # Resources need a reference to the client but do not receive a Context.
    # Kept in a mutable cell filled in by the lifespan.
    holder: dict[str, OmniRouteClient] = {}

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
        """One HTTP client for the server's whole lifecycle.

        This way TCP connections are reused and resources are guaranteed
        to be closed on shutdown. Creating a client per tool call is a
        common and expensive mistake.
        """
        client = OmniRouteClient(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.timeout,
        )
        holder["client"] = client
        log(
            f"connecting to {settings.base_url} (key: {settings.masked_key}, "
            f"default model: {settings.model or '<not set>'})"
        )
        try:
            yield AppContext(client=client, settings=settings)
        finally:
            await client.aclose()
            log("stopped")

    mcp = FastMCP(
        name="omniroute",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
    )

    register_tools(mcp)
    register_resources(mcp, lambda: holder["client"])
    register_prompts(mcp)
    return mcp


async def selftest(settings: Settings) -> int:
    """Checks all endpoints against a live OmniRoute, bypassing the MCP layer.

    A diagnostic for the lecture and for debugging: if selftest is green
    but the agent does not work, the problem is in the MCP client's
    configuration, not in the gateway. Returns the process exit code.
    """
    log(f"self-test: {settings.base_url} (key: {settings.masked_key})")
    if not settings.api_key:
        log("WARNING: OMNIROUTE_API_KEY is not set — management endpoints will return 401")

    try:
        model = settings.require_model()
    except ConfigError as exc:
        log(f"self-test cannot run: {exc}")
        return 1
    log(f"model used for the inference check: {model}")

    client = OmniRouteClient(settings.base_url, settings.api_key, settings.timeout)
    checks: list[tuple[str, object]] = [
        ("health", client.health()),
        ("models", client.models()),
        ("providers", client.providers()),
        ("quota", client.quota()),
        ("provider_stats", client.provider_stats()),
        ("call_logs", client.call_logs(3)),
        ("logs_export", client.logs_export(24)),
        (
            "chat_completion",
            client.chat_completion(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=512,
            ),
        ),
    ]

    failures = 0
    try:
        for name, coro in checks:
            try:
                result = await coro  # type: ignore[misc]
                size = len(result) if isinstance(result, (list, dict)) else 0
                log(f"  ✓ {name} (items/fields: {size})")
            except OmniRouteError as exc:
                failures += 1
                log(f"  ✗ {name}: {exc}")
    finally:
        await client.aclose()

    log(f"self-test finished: {len(checks) - failures}/{len(checks)} succeeded")
    return 1 if failures else 0


def run_stdio(settings: Settings) -> None:
    """Local version of the server: stdio transport.

    The process is launched by the agent itself; the exchange happens
    over stdin/stdout. No port, no network — isolation is provided by
    the operating system.
    """
    # Not a single character on stdout besides JSON-RPC frames (see log()).
    create_server(settings).run(transport="stdio")


def run_http(settings: Settings, host: str, port: int) -> None:
    """Network version of the server: streamable-http transport.

    The server runs on its own and listens on a port; the endpoint is
    POST /mcp. Needed when the agent cannot spawn a subprocess: a
    different container, a different machine, or several agents sharing
    one server.
    """
    mcp = create_server(settings)
    # FastMCP reads host and port from its own settings object, not from run().
    mcp.settings.host = host
    mcp.settings.port = port
    log(f"HTTP transport: http://{host}:{port}/mcp")
    mcp.run(transport="streamable-http")


def main() -> None:
    """Entry point `omniroute-mcp` — defaults to the local (stdio) version."""
    parser = argparse.ArgumentParser(
        prog="omniroute-mcp",
        description="MCP server giving local AI agents access to the OmniRoute gateway",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio (default) — launched by the agent as a subprocess; http — network access",
    )
    parser.add_argument("--host", default="127.0.0.1", help="host for --transport http")
    parser.add_argument("--port", type=int, default=8765, help="port for --transport http")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="check that OmniRoute is reachable and exit (does not start the server)",
    )
    args = parser.parse_args()

    settings = Settings.from_env()

    if args.selftest:
        sys.exit(asyncio.run(selftest(settings)))

    if args.transport == "http":
        run_http(settings, args.host, args.port)
    else:
        run_stdio(settings)


def main_http() -> None:
    """Entry point `omniroute-mcp-http` — network version without extra flags.

    A separate command exists for clarity: two ways to deploy the same
    server are visible in the script list rather than hidden behind a
    flag. The server code itself is shared — duplicating it would be a
    mistake.
    """
    parser = argparse.ArgumentParser(
        prog="omniroute-mcp-http",
        description="Network (HTTP) version of the OmniRoute MCP server",
    )
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on")
    parser.add_argument("--port", type=int, default=8765, help="port to listen on")
    args = parser.parse_args()

    run_http(Settings.from_env(), args.host, args.port)


if __name__ == "__main__":
    main()
