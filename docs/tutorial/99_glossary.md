# Glossary and project map

A reference chapter: terms with backend analogies, and a "where to find
what" table.

## MCP terms

**MCP (Model Context Protocol)** — the protocol an LLM agent uses to talk to
external tools. Technically: JSON-RPC 2.0 over stdio or HTTP. The difference
from REST: runtime discovery, and the consumer of the contract is a model,
not a program.

**MCP server** — what we wrote. Gives the agent tools, resources, prompts.

**MCP client / host** — the agent: Claude Code, Cursor, Claude Desktop, or
your own on the SDK. It launches the server and decides what to call. An
example client is `src/omniroute_agent/discovery.py`.

**tool** — an action that the **model** calls on its own decision. The
analog of an RPC method. Can change the state of the world.

**resource** — data, addressed by URI, loaded by the **client**. The analog
of a `GET` on a resource. Read-only, no side effects.

**prompt** — a task template, run by a **human**. In Claude Code it shows up
as a slash command. Defines a method for applying tools.

**initialize** — the first call of a session: negotiates protocol version
and capabilities. Roughly analogous to a TLS handshake: nothing can happen
before it.

**tools/list** — a request for the tool catalog with schemas. The closest
analogy is requesting an OpenAPI spec, except it happens on every startup.

**tools/call** — calling a tool by name with arguments.

**isError** — a flag on a `tools/call` response. Means the tool failed; the
error text still arrives as regular content, and the model can read it.

**stdio transport** — the server is launched by the agent as a child
process, exchange happens over stdin/stdout. No port, no network. The
default mode. Analogous to CGI.

**streamable-http transport** — the server listens on a port, accepts
JSON-RPC on `POST /mcp`. Needed when the agent cannot spawn a subprocess.

**FastMCP** — the high-level API of the official Python `mcp` SDK. Builds
tool schemas from function signatures. In spirit, like Spring Web MVC on top
of servlets.

**Context** — an object FastMCP hands to a tool if it declares a parameter
of that type. Used to reach the lifespan context. The analog of an injected
dependency.

**lifespan** — manages the server's lifecycle: initialization at startup,
cleanup at shutdown. The analog of `@PostConstruct` / `@PreDestroy`.

**instructions** — a description of the server as a whole, given to the
agent at `initialize`. One level above the docstrings of individual tools.

## OmniRoute terms

**OmniRoute** — a local LLM gateway. An API gateway behind which sit dozens
of models from different providers, with routing, fallbacks, circuit
breakers, quotas, and token accounting.

**Provider** — a model supplier: Anthropic, OpenAI, Moonshot, etc. In the
OmniRoute API, configured connections are called `connections`.

**Combo / `auto/*`** — a routing strategy, not a model. `auto/cheap`,
`auto/best-coding`: the gateway itself picks the provider behind a name like
this. The most convenient entry point for an agent.

**Route class** — OmniRoute's authorization mechanism, relevant to our
client:

| Class | Examples | Requires |
|---|---|---|
| `PUBLIC` | `/api/monitoring/health` | nothing |
| `CLIENT_API` | `/v1/chat/completions`, `/v1/models` | a Bearer key |
| `MANAGEMENT` | `/api/providers`, `/api/usage/*` | a session or a Bearer key with the `manage` scope |

**`manage` scope** — a key's right to access management endpoints. A key
with this scope covers all three classes, which is why our client stays
stateless.

**reasoning tokens** — a model's hidden reasoning, which consumes the
`max_tokens` budget before the visible answer starts. The reason the
project's default `max_tokens` is 1024.

**`streamDefaultMode: "legacy"`** — a key mode in which the gateway defaults
to SSE responses. Because of it, `chat_completion` hardcodes `"stream":
False`.

## Async Python terms

**coroutine** — what calling an `async def` function returns. **Does not
execute** until `await`. Forgetting `await` is a classic mistake — the code
silently does nothing.

**`await`** — hand control back to the event loop and wait for the result.

**event loop** — a single-threaded scheduler for cooperative multitasking.
Like Node.js or Netty: while waiting on I/O, other tasks run.

**`async with`** — try-with-resources for asynchronous resources.

**`@asynccontextmanager`** — turns a generator into an async resource:
initialization before `yield`, cleanup in `finally`.

**`httpx.AsyncClient`** — an async HTTP client with a connection pool and
keep-alive. The analog of `requests.Session`, but async.

**`respx`** — a library for replacing httpx's transport in tests. The analog
of WireMock.

**`@dataclass(frozen=True)`** — an immutable data class. The analog of a Java
record.

**decorator** — a function that takes a function and does something with
it. `@mcp.tool()` plays a role similar to a `@GetMapping` annotation.

## Where to find what

| Need to | File |
|---|---|
| Add a tool | `src/omniroute_mcp/tools.py` |
| Add an OmniRoute endpoint | `src/omniroute_mcp/client.py` |
| Change the output format | `src/omniroute_mcp/formatting.py` |
| Add an environment variable | `src/omniroute_mcp/config.py` |
| Change assembly or transport | `src/omniroute_mcp/server.py` |
| Add a resource | `src/omniroute_mcp/resources.py` |
| Add a prompt scenario | `src/omniroute_mcp/prompts.py` |
| Understand how the agent connects | `src/omniroute_agent/discovery.py` |
| Quick start and configs | `README.md` |

## How to add a new tool

The practical sequence — four steps, bottom-up through the layers:

**1. A method on the client** (`client.py`), in the right section:

```python
async def sessions(self) -> list[dict[str, Any]]:
    """Active gateway sessions."""
    data = await self._get("/api/sessions")
    return data.get("sessions", [])
```

**2. A formatter** (`formatting.py`) — a pure `dict → str` function. Remember
the context hygiene from chapter 3: strip out anything the agent won't base
a decision on.

**3. A tool** (`tools.py`) inside `register_tools`:

```python
@mcp.tool()
async def omniroute_sessions(ctx: Context) -> str:
    """Show active gateway sessions.

    Call this when you need to know who is currently using the gateway.
    """
    return formatting.format_sessions(await _client(ctx).sessions())
```

The docstring is the most important part here. Write **when to use it**, not
how it's implemented.

**4. Tests** (`tests/`) — on the client via `respx`, on the formatter
directly. Don't forget negative assertions for absence of noise.

Additionally: add the method to the `checks` list in `selftest`
(`server.py`), so the new endpoint gets checked against the live gateway.

## Commands

```bash
uv sync --extra dev                      # install dependencies
uv run omniroute-mcp --selftest          # check the connection to the gateway (8/8)
uv run pytest -q                         # tests (24, without OmniRoute)
claude mcp list                          # check the connection in Claude Code

# two server versions
uv run omniroute-mcp                     # local, stdio (usually not run by hand)
uv run omniroute-mcp-http --port 8765    # network, http://127.0.0.1:8765/mcp

# examples
uv run omniroute-discover   # a bare MCP client
uv run omniroute-agent        # agent through the local version
uv run omniroute-agent-http         # agent through the network version
```

## Seven takeaways from the project

If you remember only this from the whole tutorial:

1. **A tool's schema = the function signature.** We never write separate schemas.
2. **The docstring is a prompt.** The model reads it, and it shapes the
   model's behavior.
3. **The quality of an MCP server is defined by what it does NOT put into
   the context.**
4. **Fewer tools → more accurate selection.** 8 beats 104.
5. **Error text is an interface for the model.** Write in it what to do next.
6. **In stdio, stdout belongs to the protocol.** Logs only go to stderr.
7. **Pull the contract from the live service, not from the docs.** That's
   how `stream: false`, the `max_tokens` trap, and the mismatch between
   `/api/models` and `/v1/models` were found.

---

**Back:** [Chapter 0. Overview](00_overview.md)
