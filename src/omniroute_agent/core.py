"""The core of a minimal autonomous agent.

**All of the agent's logic** lives here. The `stdio.py` and `http.py`
modules differ in exactly one thing — how they connect to the MCP
server. That is the whole point: the agent's code does not depend on
the transport.

What an agent is, in its simplest form — three parts:

    1. BRAIN — a language model (obtained through OmniRoute);
    2. HANDS — tools (obtained through MCP);
    3. LOOP  — the model asks to call a tool, we call it, feed the
               result back, and repeat until it produces a final answer.

Notice the elegance of the design: OmniRoute is used twice here — both
as the agent's brain (via `/v1/chat/completions`) and as the source of
its tools (via our MCP server).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from mcp import ClientSession

from omniroute_mcp.client import OmniRouteClient, OmniRouteError
from omniroute_mcp.config import ConfigError, Settings

# Default task — the same diagnostic scenario from the lecture. Kept as a
# default rather than a hardcoded constant so it can now be overridden by
# the command's first argument.
DEFAULT_TASK = (
    "Check the status of the OmniRoute gateway and tell me whether it is healthy. "
    "If there are any problem providers, name them."
)

DEFAULT_MAX_STEPS = 6

SYSTEM_PROMPT = """\
You are a diagnostic agent for the OmniRoute gateway. You have tools \
to check its status. Call them to get actual data rather than guessing. \
Once you have enough information, give a short, coherent answer. \
Do not just restate the raw tool output — draw conclusions.\
"""


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def say(message: str = "") -> None:
    """Prints the agent's progress.

    The agent is a regular program, so stdout is free to use here. The
    "stderr only" rule applies inside the MCP server in stdio mode, not
    in its clients.
    """
    print(message, flush=True)


# ----------------------------------------------------------------------
# Bridge between MCP and the OpenAI function-calling format
# ----------------------------------------------------------------------


def mcp_tools_to_openai(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    """Translates MCP tools into the OpenAI `tools` format.

    Both sides speak JSON Schema, so the translation is almost just
    repackaging fields:

        MCP                     OpenAI
        tool.name          ->   function.name
        tool.description   ->   function.description
        tool.inputSchema   ->   function.parameters

    This triviality is exactly what makes MCP useful: the server
    describes its tools once, and they can be plugged into any model
    that supports function calling.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": (tool.description or "").strip(),
                "parameters": tool.inputSchema,
            },
        }
        for tool in mcp_tools
    ]


def extract_text(result: Any) -> str:
    """Pulls text out of a `tools/call` result.

    An MCP response is a list of content blocks (can be text, images,
    or resource links). Our server always returns text, but the code
    should survive other block types too.
    """
    parts = [block.text for block in result.content if hasattr(block, "text")]
    return "\n".join(parts) if parts else "<empty tool response>"


# ----------------------------------------------------------------------
# The agent loop
# ----------------------------------------------------------------------


async def run_agent(
    session: ClientSession,
    task: str,
    model: str | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> str:
    """Runs the task, calling MCP tools until an answer is ready.

    Args:
        session: an already-initialized MCP session (transport does not matter).
        task: the task, in natural language.
        model: the "brain" model, needs function-calling support;
            if omitted, CLIENT_MODEL from the configuration is used.
        max_steps: a safeguard against an infinite loop.

    Returns:
        The agent's final text answer.

    Raises:
        ConfigError: no model was given, either as an argument or in .env.
    """
    settings = Settings.from_env()
    model = model or settings.require_model()
    llm = OmniRouteClient(settings.base_url, settings.api_key, settings.timeout)

    try:
        # STEP 1. Discovery: learn the tools at runtime.
        # The agent does not know them in advance — that is the whole point of MCP.
        mcp_tools = (await session.list_tools()).tools
        openai_tools = mcp_tools_to_openai(mcp_tools)
        say(f"Tools received: {len(openai_tools)}")
        say(f"Brain model: {model}")
        say(f"Task: {task}\n")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]

        # STEP 2. The "model <-> tools" loop.
        for step in range(1, max_steps + 1):
            response = await llm.chat_completion(
                model=model,
                messages=messages,
                tools=openai_tools,
                max_tokens=2000,
            )
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls")

            # No tool calls — the model is ready to answer.
            if not tool_calls:
                content = message.get("content") or "<empty model response>"
                say(f"[step {step}] final answer (model: {response.get('model')})")
                return content

            # The model's response requesting the calls is stored in history
            # in full: without it, the next turn would not know where the
            # tool results came from.
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )

            for call in tool_calls:
                name = call["function"]["name"]
                raw_args = call["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    # The model generated invalid JSON. Do not crash:
                    # tell it about the problem, and it usually corrects itself.
                    args = {}
                    say(f"[step {step}] ⚠ invalid arguments for {name}: {raw_args!r}")

                say(f"[step {step}] calling {name}({json.dumps(args, ensure_ascii=False)})")

                result = await session.call_tool(name, args)
                text = extract_text(result)
                if result.isError:
                    # A tool error is handed to the model as a regular result:
                    # the text is written so the model can react on its own.
                    say(f"[step {step}] ⚠ tool returned an error")

                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": text}
                )

        # STEP 3. The safeguard tripped.
        return (
            f"The agent did not finish within {max_steps} steps. This happens "
            "when the task is too broad or the model gets stuck looping on tools."
        )
    finally:
        await llm.aclose()


def resolve_model(explicit: str | None = None) -> str:
    """Determines the model BEFORE opening the MCP session.

    Why before rather than inside: a `sys.exit()` from within `async with`
    travels through anyio's task group and turns into a
    `BaseExceptionGroup` — the user sees fifty lines of traceback instead
    of one line. Configuration errors are caught up front.
    """
    return explicit or Settings.from_env().require_model()


async def run_session(
    session: ClientSession,
    transport_name: str,
    task: str,
    model: str,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> int:
    """The shared scenario for both agent versions: connect and run the task.

    This used to be called `demo` and had a hardcoded task string. Now
    the task is a parameter: the same code can both show the lecture
    demo and do real work.

    Returns:
        The process exit code. Returned rather than passed to `sys.exit`
        directly: exiting from the middle of a session breaks proper
        transport shutdown.
    """
    info = await session.initialize()
    say("=" * 64)
    say(f"Agent connected to server '{info.serverInfo.name}' over {transport_name} transport")
    say("=" * 64)

    try:
        answer = await run_agent(session, task, model=model, max_steps=max_steps)
    except OmniRouteError as exc:
        # The agent's brain lives in the same OmniRoute as its tools.
        # If the gateway is unreachable, explain it in plain terms.
        say(f"\nFailed to complete the task: {exc}")
        return 1

    say("\n" + "-" * 64)
    say(answer)
    say("-" * 64)
    return 0


def run_cli(parser: argparse.ArgumentParser, connect) -> None:
    """The shared entry-point wiring for both agent versions.

    Order matters: first parse arguments and configuration, and only
    then connect asynchronously. `connect(args, model)` returns a
    coroutine that runs the session.
    """
    args = parser.parse_args()

    try:
        model = resolve_model(args.model)
    except ConfigError as exc:
        say(f"Configuration error: {exc}")
        sys.exit(2)

    sys.exit(asyncio.run(connect(args, model)))


def build_arg_parser(prog: str, description: str) -> argparse.ArgumentParser:
    """Shared command-line arguments for both agent versions.

    One parser for two transports — so the flags do not drift apart.
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "task",
        nargs="?",
        default=DEFAULT_TASK,
        help="the task for the agent, in natural language (defaults to a gateway diagnostic)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="the \"brain\" model; defaults to CLIENT_MODEL from .env",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help=f"safeguard against looping (default {DEFAULT_MAX_STEPS})",
    )
    return parser
