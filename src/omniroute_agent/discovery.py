"""How to connect to an MCP server from your own code.

The other side of the lecture: so far we have been writing the SERVER,
here it is the CLIENT. This is exactly what Claude Code, Cursor, and
Claude Desktop do internally.

Run:
    omniroute-discover

The command runs through a full MCP session lifecycle:
    1. spawns the server as a subprocess (stdio);
    2. performs the `initialize` handshake;
    3. asks for the list of primitives (tools / resources / prompts);
    4. reads a resource, calls a tool, renders a prompt.

Each of the three MCP primitives is initiated by a different side: a
tool by the model, a resource by the client/user (like a file
attachment), a prompt by a human (like a slash command). Below is a
client-side call for each of the three — on the server they correspond
to tools.py, resources.py, and prompts.py.
"""

from __future__ import annotations

import asyncio

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl

from .stdio import server_parameters


async def main_async() -> None:
    # HOW TO LAUNCH the server is described once in stdio.py — reused here.
    # There is no network involved: communication goes through the child
    # process's stdin/stdout.
    async with stdio_client(server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            # 1. Handshake: negotiate protocol version and capabilities.
            info = await session.initialize()
            print(f"Connected to: {info.serverInfo.name}")

            # 2. Discovery. The agent does not know the tools in advance —
            #    it asks about them at runtime. That is the whole point of
            #    MCP: a new server = new capabilities without touching the
            #    agent's code.
            tools = (await session.list_tools()).tools
            print(f"\nAvailable tools ({len(tools)}):")
            for tool in tools:
                first_line = (tool.description or "").strip().split("\n")[0]
                print(f"  - {tool.name} — {first_line}")

            resources = (await session.list_resources()).resources
            print(f"\nResources ({len(resources)}): {', '.join(str(r.uri) for r in resources)}")

            prompts = (await session.list_prompts()).prompts
            print(f"Prompts ({len(prompts)}): {', '.join(p.name for p in prompts)}")

            # 3. Reading a resource. In Claude Code the user does this via
            #    @omniroute:health — it preloads data into the context
            #    AHEAD OF TIME, without spending a model turn. Here we
            #    emulate the same thing in code: read_resource() expects an
            #    AnyUrl, not a bare string.
            print("\n--- resource omniroute://health ---")
            resource_result = await session.read_resource(AnyUrl("omniroute://health"))
            print(resource_result.contents[0].text)

            # 4. Calling a tool. In a real agent the decision to call and
            #    the arguments are made up by the MODEL; here we supply them by hand.
            print("\n--- omniroute_health ---")
            result = await session.call_tool("omniroute_health", {})
            print(result.content[0].text)

            # 5. Delegating a task to another model through the gateway.
            #    No model is passed: the server will use CLIENT_MODEL from .env.
            print("\n--- omniroute_chat (model from configuration) ---")
            result = await session.call_tool(
                "omniroute_chat",
                {
                    "prompt": "Explain what MCP is in one sentence.",
                    "max_tokens": 600,
                },
            )
            # isError=True means the tool failed; the error text arrives in
            # the same content field — the model would be able to read it.
            print(result.content[0].text)

            # 6. Rendering a prompt. In Claude Code a human does this via
            #    the slash command /mcp__omniroute__diagnose_gateway — the
            #    server responds with a ready-made instruction text rather
            #    than executing it itself. get_prompt() only renders the
            #    template; calling the tools it describes (steps 1-5 in the
            #    text) is up to the agent/LLM itself.
            print("\n--- diagnose_gateway prompt ---")
            prompt_result = await session.get_prompt("diagnose_gateway")
            print(prompt_result.messages[0].content.text)


def main() -> None:
    """Entry point for the `omniroute-discover` console command."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
