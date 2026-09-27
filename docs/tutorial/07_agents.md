# Chapter 7. Agents: two server versions and the autonomy loop

So far we've been writing the **server**. This chapter is about the
consumer: the agent. Let's look at how the two server versions differ, what a
minimal autonomous agent is made of, and why its code does not depend on the
transport.

## Two versions of one server

Both versions are `create_server()` from chapter 5, run differently:

```python
def run_stdio(settings: Settings) -> None:
    create_server(settings).run(transport="stdio")


def run_http(settings: Settings, host: str, port: int) -> None:
    mcp = create_server(settings)
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="streamable-http")
```

`src/omniroute_mcp/server.py`

The difference is five lines. Tools, resources, prompts, formatters, and the
client are identical.

Each version gets its own console command (`pyproject.toml`):

```toml
omniroute-mcp = "omniroute_mcp.server:main"            # local: stdio
omniroute-mcp-http = "omniroute_mcp.server:main_http"  # network: streamable-http
```

A note on the decision: "two versions" here means **two ways to deploy the
same code**, not two projects. Copying the server into a separate file would
have been a mistake — it would mean keeping eight tools in sync in two
places. Separate commands exist purely for visibility: both deployment
options show up in the script list instead of being hidden behind a
`--transport` flag.

### How they differ in practice

| | Local (stdio) | Network (HTTP) |
|---|---|---|
| Who starts it | the agent itself, as a subprocess | you, ahead of time |
| Channel | stdin / stdout | `POST /mcp` |
| Lifetime | dies with the agent | lives independently |
| How many agents | one per process | several per server |
| Where the OmniRoute key lives | passed by the agent via `env` | configured on the server |
| Authentication | not needed | **none — must not be exposed** |

Two consequences worth spelling out.

**The security model is different.** In stdio, the operating system provides
it: the process is yours, and only the agent that spawned it has access. The
HTTP version has no authentication at all — anyone who can reach the port
gets your OmniRoute key packaged as ready-to-use tools. That's why the
default host is `127.0.0.1`, and why a production deployment needs a reverse
proxy with authorization in front of it.

**Ownership of the key is different.** In stdio, the agent passes the key via
`env`, and each agent can have its own. In the HTTP version, the key is
configured on the server, and every connected agent operates under it. That
is convenient (an agent only needs to know the URL) and risky at the same
time — a shared account.

## What an agent is, at minimum

Three components:

1. **Brain** — a language model. Obtained through OmniRoute
   (`/v1/chat/completions`).
2. **Hands** — tools. Obtained through MCP.
3. **Loop** — the model asks to call a tool, we call it, feed the result
   back, and repeat until a final answer appears.

Notice the elegance of the design: **OmniRoute is used twice** — as the
agent's brain and as the source of its tools.

For a backend developer, here's a useful framing: an agent is a `while` loop
in which the model acts as a planner. It does not execute actions, it
**requests** them, and your regular, deterministic code executes them.

## The bridge between MCP and the OpenAI format

The first thing the agent does is translate MCP tools into a format the
model understands:

```python
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
```

`src/omniroute_agent/core.py`

The translation is just repackaging three fields, because both sides speak
JSON Schema. This triviality is exactly what makes MCP valuable: the server
describes its tools once, and they can be plugged into any model that
supports function calling.

Recall chapter 4: FastMCP built `inputSchema` from the Python function's
signature. The chain is end to end: **type annotations → MCP JSON Schema →
OpenAI format → the model's decision.** The schema is never written by hand
at any step.

## The loop

The heart of the agent (`src/omniroute_agent/core.py`), step by step.

**Discovery.** Tools are requested at runtime, not baked into the code:

```python
mcp_tools = (await session.list_tools()).tools
openai_tools = mcp_tools_to_openai(mcp_tools)
```

**Exit condition.** The model returned text with no `tool_calls` — the work
is done:

```python
if not tool_calls:
    content = message.get("content") or "<empty model response>"
    return content
```

**Growing the history.** The model's response requesting the calls is stored
in history in full — including `tool_calls`:

```python
messages.append({
    "role": "assistant",
    "content": message.get("content") or "",
    "tool_calls": tool_calls,
})
```

This is a non-obvious spot. Without the assistant message, the next turn
would not know where the results with `role: "tool"` came from, and providers
would return a validation error. The history has to be coherent: a call
request followed by its result.

**Execution and returning the result:**

```python
result = await session.call_tool(name, args)
text = extract_text(result)
messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
```

`tool_call_id` ties a result to a specific request — in a single turn the
model may ask for several calls at once, and the order of results is not
guaranteed.

## Three places where the agent has to be suspicious

The model generates its own actions **probabilistically**. The code around it
has to account for that — otherwise the agent falls apart on the first
unusual turn.

**A loop safeguard** (`core.py`):

```python
max_steps: int = 6,
```

An agent is a loop, and a loop must have a limiter. Without one, a model
stuck cycling through tools would keep going until the money runs out.
Returning when the limit is reached is not an exception — it's a clear
message.

**Invalid JSON in the arguments** (`core.py`):

```python
try:
    args = json.loads(raw_args)
except json.JSONDecodeError:
    args = {}
    say(f"[step {step}] ⚠ invalid arguments for {name}: {raw_args!r}")
```

The model sometimes generates broken JSON. It must not crash — we report it
and move on; usually the model corrects itself on the next turn.

**A tool error is a result too** (`core.py`):

```python
if result.isError:
    say(f"[step {step}] ⚠ tool returned an error")

messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
```

On `isError`, the text still goes into history. This is where all the work
from chapter 2 pays off: a message like "provide OMNIROUTE_API_KEY with the
manage scope" lands directly in the model's context, and it can report a
meaningful reason to the user instead of "the tool failed."

## The transport doesn't affect the logic

The main observation of this chapter. Here is the entire difference between
the two agents:

```python
# stdio.py — the agent spawns the process itself
async with stdio_client(server) as (read, write):
    async with ClientSession(read, write) as session:
        await run_session(session, transport_name="stdio (local subprocess)")

# http.py — the agent connects to an already-running server
async with streamablehttp_client(url) as (read, write, _get_session_id):
    async with ClientSession(read, write) as session:
        await run_session(session, transport_name=f"streamable-http ({url})")
```

`src/omniroute_agent/stdio.py` and `src/omniroute_agent/http.py`

The difference is in how the session is constructed (and the fact that the
HTTP transport returns a third value, a function for getting the session
id). From there, both versions call the same `run_session()` and the same
`run_agent()`.

A familiar idea: `ClientSession` is an interface, transports are its
implementations. Applied to agents, it has a practical consequence: **an
agent written once works with any MCP server and any transport.** That's the
whole point of the protocol.

## What a live run showed

A local agent given the task "check the gateway's status and name any
problem providers":

```
Tools received: 8

[step 1] calling omniroute_health({})
[step 1] calling omniroute_list_providers({})
[step 2] final answer (model: big-pickle)
```

Two observations worth noting.

**The agent chose the tools itself.** They weren't named in the task. It
matched the wording against the docstring descriptions and decided it needed
exactly these two. This is a direct payoff from the work on descriptions
(chapter 4): without the words "problem providers" and "gateway status" in
them, the choice would have been worse.

**Two calls in one turn.** The model requested both tools at once, without
waiting for the first result. Hence the `for call in tool_calls` loop —
you have to handle a list, not a single call.

The HTTP version, given the same task, used only one tool and gave a shorter
answer. That's normal: the model is non-deterministic, and **an agent's
behavior is not guaranteed to be reproducible** — one more reason automated
tests target the deterministic parts of the system (chapter 6), not the
model's behavior.

## Summary

- Two server versions are two ways to deploy shared code; duplicating the
  server would be a mistake.
- stdio and HTTP differ in security model and key ownership, not in
  capabilities.
- Agent = brain (LLM) + hands (MCP) + loop. Everything else in frameworks is
  scaffolding.
- The MCP → OpenAI translation is trivial because both sides use JSON Schema.
- The agent's code has to be suspicious: a step limiter, broken JSON, tool
  errors.
- The agent's logic doesn't depend on the transport — that's the point of
  the protocol.

---

**Next:** [Glossary](99_glossary.md) — terms and a map of "where to find what".
