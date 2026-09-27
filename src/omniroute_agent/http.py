"""An agent that talks to the NETWORK (HTTP) version of the MCP server.

Run in two terminals:

    # terminal 1 — the server runs on its own
    omniroute-mcp-http --port 8765

    # terminal 2 — the agent connects to it over the network
    omniroute-agent-http

Differences from the stdio version:

    - the server must be started AHEAD OF TIME, it is not spawned by the agent;
    - the server outlives the agent: several agents can connect to the
      same server, one after another or at the same time;
    - the environment (including the OmniRoute key) is configured on the
      SERVER, not passed by the agent — the agent only needs to know the URL.

All of the agent's logic lives in core.py, and it is the same code.
Compare this file to stdio.py: the only difference is how the session is created.
"""

from __future__ import annotations

import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from omniroute_mcp.envfile import load_env_file

from .core import build_arg_parser, run_cli, run_session, say

DEFAULT_URL = "http://127.0.0.1:8765/mcp"


def default_url() -> str:
    """Address of the MCP server: OMNIROUTE_MCP_URL from the environment or .env."""
    load_env_file()
    return os.environ.get("OMNIROUTE_MCP_URL", "").strip() or DEFAULT_URL


async def ensure_server_is_up(url: str) -> None:
    """Checks ahead of time that the server is running, and explains what to do if not.

    Without this check, the user would get a confusing connection error
    from deep inside the MCP SDK. The network transport differs from
    stdio precisely because the server is an external dependency and
    needs to be checked.
    """
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            await http.get(url)
    except httpx.ConnectError:
        say(f"MCP server is not reachable at {url}")
        say("Start it in a separate terminal:")
        say("    omniroute-mcp-http --port 8765")
        sys.exit(1)


async def connect(args, model: str) -> int:
    """Connects to an already-running server and hands the session to the scenario."""
    url = args.url or default_url()
    await ensure_server_is_up(url)

    # This transport returns three values (the third is a function to get
    # the session id), unlike the two from stdio. From here the code is identical.
    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            return await run_session(
                session,
                transport_name=f"streamable-http ({url})",
                task=args.task,
                model=model,
                max_steps=args.max_steps,
            )


def main() -> None:
    """Entry point for the `omniroute-agent-http` console command."""
    parser = build_arg_parser(
        prog="omniroute-agent-http",
        description="Autonomous agent on top of the network (HTTP) OmniRoute MCP server",
    )
    parser.add_argument(
        "--url",
        default=None,
        help=f"address of the MCP server (defaults to OMNIROUTE_MCP_URL or {DEFAULT_URL})",
    )
    run_cli(parser, connect)


if __name__ == "__main__":
    main()
