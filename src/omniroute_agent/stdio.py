"""An agent that talks to the LOCAL (stdio) version of the MCP server.

Run:

    omniroute-agent
    omniroute-agent "How many models are available and which providers are failing?"

Transport quirk: the server **does not need to be started beforehand**.
The agent spawns it as a child process and talks to it over
stdin/stdout. When the agent exits, the server dies with it.

This is how Claude Code, Claude Desktop, and Cursor work.

All the agent's logic lives in core.py. This file only sets up the connection.
"""

from __future__ import annotations

import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from omniroute_mcp.envfile import ENV_FILE_VAR, find_env_file

from .core import build_arg_parser, run_cli, run_session


def server_parameters() -> StdioServerParameters:
    """Describes HOW TO LAUNCH the server.

    This is exactly what goes into the `mcpServers` config for Claude
    Desktop and Cursor: command, arguments, environment.

    We launch `python -m omniroute_mcp.server` with the current
    interpreter rather than `uv run --directory <repo root>`. The
    difference is that the former works both for an installed package
    and from any working directory, and does not require `uv` to be
    present on the system at all.
    """
    env = dict(os.environ)

    # Tell the subprocess where to find .env: it searches upward from its
    # own working directory, which for a spawned process can be anything.
    env_file = find_env_file()
    if env_file is not None:
        env[ENV_FILE_VAR] = str(env_file)

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "omniroute_mcp.server"],
        env=env,
    )


async def connect(args, model: str) -> int:
    """Spawns the server subprocess and hands the session to the shared scenario."""
    # stdio_client spawns the process and returns a pair of read/write channels.
    async with stdio_client(server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            return await run_session(
                session,
                transport_name="stdio (local subprocess)",
                task=args.task,
                model=model,
                max_steps=args.max_steps,
            )


def main() -> None:
    """Entry point for the `omniroute-agent` console command."""
    run_cli(
        build_arg_parser(
            prog="omniroute-agent",
            description="Autonomous agent on top of the local (stdio) OmniRoute MCP server",
        ),
        connect,
    )


if __name__ == "__main__":
    main()
