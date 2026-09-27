# Agent: two server versions and their clients

This used to live in `examples/` and ran as loose scripts. Now the client is
an installable package just like the server (`src/omniroute_agent/`), with
its own console commands. The model, the key, and the gateway address are
read by both halves from the same `.env` (see `CLIENT_MODEL`).

## Two versions of the MCP server

This is **the same server** with the same set of 8 tools. Only the
transport — the way the agent reaches it — differs.

| | Local (stdio) | Network (HTTP) |
|---|---|---|
| Command | `omniroute-mcp` | `omniroute-mcp-http` |
| Who launches it | the agent itself, as a subprocess | you, ahead of time |
| Channel | stdin / stdout | `POST http://host:port/mcp` |
| Port | not needed | needed (default 8765) |
| Lifetime | dies with the agent | runs independently |
| How many agents | one per process | several per server |
| Where the OmniRoute key lives | passed in by the agent via `env` | configured on the server |
| Authentication | not needed (OS-level isolation) | **none — do not expose publicly** |
| When to use | Claude Code, Cursor, Desktop | agent in a container or on another machine |

Why two commands and not two projects: duplicating the server code would be
a mistake. The shared part is `create_server()`; `run_stdio()` and
`run_http()` (`src/omniroute_mcp/server.py:166`) differ by five lines.
Separate console commands exist so both ways of running the server show up
in the script list, instead of being hidden behind a flag.

## Package layout

| Module | Command | What it does |
|---|---|---|
| `discovery.py` | `omniroute-discover` | a bare MCP client: handshake, discovery, tool call |
| `stdio.py` | `omniroute-agent` | autonomous agent over the local version |
| `http.py` | `omniroute-agent-http` | autonomous agent over the network version |
| `core.py` | — | **the agent's logic**, shared by both versions |

## Running it

**Local agent** — nothing needs to be started ahead of time:

```bash
uv run omniroute-agent
uv run omniroute-agent "Which providers are failing and why?"
```

**Network agent** — two terminals:

```bash
# terminal 1
uv run omniroute-mcp-http --port 8765

# terminal 2
uv run omniroute-agent-http
```

The address can be overridden: `OMNIROUTE_MCP_URL=http://host:8765/mcp`
or the `--url` flag.

## Command-line arguments

Shared by both versions (`build_arg_parser` in `core.py`):

| Argument | Default | Meaning |
|---|---|---|
| `task` (positional) | gateway diagnostic | the agent's task, in natural language |
| `--model` | `CLIENT_MODEL` from `.env` | the "brain" model; needs function-calling support |
| `--max-steps` | 6 | a safeguard against looping |

Exit codes: `0` — success, `1` — gateway failure or server unreachable,
`2` — configuration error (e.g. `CLIENT_MODEL` not set).

The model is resolved **before** the MCP session is opened. This is not
pedantry: a `sys.exit()` from inside `async with` travels through anyio's
task group and turns into a `BaseExceptionGroup` — instead of one clear
line, the user gets fifty lines of traceback.

## What an agent is, in its simplest form

Three parts, all visible in `core.py`:

1. **Brain** — a language model, obtained through OmniRoute;
2. **Hands** — tools, obtained through MCP;
3. **Loop** — the model asks to call a tool → we call it → feed the result
   back → repeat, until a final answer appears.

The elegance of the design is that OmniRoute is used **twice** here: as the
agent's brain, and as the source of its tools.

### The loop, in pseudocode

```
tools = session.list_tools()                  # runtime discovery
messages = [system, task]

repeat up to max_steps:
    response = LLM(messages, tools=tools)
    if response has no tool_calls:
        return response text                  # the agent is done
    messages += model's response
    for each tool call:
        result = session.call_tool(name, arguments)
        messages += result
```

The whole autonomous agent is a `while` loop wrapped around a model call.
Everything else in popular frameworks is scaffolding around these fifteen
lines.

## The key observation

Compare `stdio.py` and `http.py`. They differ **only in how the session is
created**:

```python
# stdio: the agent spawns the process itself
async with stdio_client(server_params) as (read, write):

# http: the agent connects to an already-running server
async with streamablehttp_client(url) as (read, write, _get_session_id):
```

From there, both versions call the same `run_session(session, …)` and the
same `run_agent()`. **The agent's logic does not depend on the
transport** — that is the whole point of MCP.

## Example output

```
================================================================
Agent connected to server 'omniroute' over stdio transport
================================================================
Tools received: 8
Brain model: kr/claude-sonnet-4.5
Task: Check the status of the OmniRoute gateway and tell me whether it is healthy...

[step 1] calling omniroute_health({})
[step 1] calling omniroute_list_providers({})
[step 2] final answer (model: claude-sonnet-4.5)

----------------------------------------------------------------
The OmniRoute gateway is healthy.
- Version: 3.8.42, uptime over a day
- Active connections: several

Problem provider:
⚠️ cline — state HALF_OPEN (several failures). Last error: server_error…
----------------------------------------------------------------
```

Note the first step: the agent decided **on its own** to call two tools at
once, even though neither was named in the task. It picked them from the
docstring descriptions — that is exactly the work those descriptions are
written for, aimed at the model rather than a human reader.

## Details worth looking at in the code

**Translating MCP tools to the OpenAI format** (`core.py:71`,
`mcp_tools_to_openai`). Both sides speak JSON Schema, so the translation is
just repackaging three fields: `name`, `description`, `inputSchema` →
`parameters`. It is exactly this triviality that makes MCP useful: describe
the tools once, plug them into any model.

**Tool errors are handed to the model** (`core.py:197`, the `result.isError`
check). If a tool call fails, we still put the text into the history. Error
text is written so the model can react on its own — see point 5 in the main
README.

**The `max_steps` safeguard** (`core.py:155`, the `for step in
range(1, max_steps + 1)` loop). An agent is a loop, and a loop must have a
limiter. Without one, a model stuck looping on tools would keep going until
the budget runs out.

**Invalid JSON in arguments** (`core.py:186`, the
`except json.JSONDecodeError` branch). The model generates arguments
probabilistically and sometimes gets it wrong. Instead of crashing, we tell
it about the problem.

## Requirements

The "brain" model must support function calling: in OmniRoute's catalog,
such models have `capabilities.tool_calling: true`. The name is read from
`CLIENT_MODEL` in `.env` — there is no default in the code.

Specify a concrete model rather than an `auto/*` alias: the gateway resolves
aliases to a provider on the fly and sometimes returns `400 Invalid model`.
