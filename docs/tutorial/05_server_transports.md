# Chapter 5. Server assembly and transports

The top layer: where everything is wired together, how the client's lifecycle
is managed, and how stdio differs from HTTP. This is also where the most
common beginner mistake in MCP lives.

## A factory instead of a global object

```python
def create_server(settings: Settings | None = None) -> FastMCP:
    settings = settings or Settings.from_env()
```

`src/omniroute_mcp/server.py`

The server is created by a function, not as a module-level global variable.
Settings can be passed in from outside, and only if there are none, the
environment gets read.

The motive is familiar: **testability**. A global object that reads the
environment on import cannot be assembled twice in a test with different
configs. A factory with an optional parameter is the standard
application-factory pattern (Flask and FastAPI do the same).

## Lifespan: a 20-line DI container

The most substantial part of the module.

```python
@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    client = OmniRouteClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.timeout,
    )
    holder["client"] = client
    log(f"connecting to {settings.base_url} (key: {settings.masked_key}, ...)")
    try:
        yield AppContext(client=client, settings=settings)
    finally:
        await client.aclose()
        log("stopped")
```

`server.py`

Read it like this: the code **before** `yield` is startup initialization;
what is handed over through `yield` becomes available to handlers as context;
the code in `finally` is cleanup on shutdown.

A full analogy from your world: `@PostConstruct` / `@PreDestroy` on a
singleton, or a pair "create the pool at startup — close it at shutdown".
`try/finally` guarantees cleanup even if an exception happens inside — this is
the `async` version of try-with-resources.

**What actually lives for the long haul:** one `httpx.AsyncClient` per
process. That means TCP connections to OmniRoute are reused across tool
calls. Creating a client per call is a common mistake that adds a handshake
to every request.

The context object is a plain dataclass (`server.py`):

```python
@dataclass
class AppContext:
    client: OmniRouteClient
    settings: Settings
```

This is exactly what `_client(ctx)` from chapter 4 retrieves, by walking
`ctx.request_context.lifespan_context.client`.

### About `holder` — it's a workaround, and that's fine

```python
holder: dict[str, OmniRouteClient] = {}
```

`server.py`

A mutable dict that `lifespan` fills in, and that the resources' lambda later
reads from. It looks inelegant, so here is the reasoning.

Tools get the client through `Context` — clean and idiomatic. **Resources do
not receive a `Context`** (a FastMCP limitation). And the client cannot be
passed into `register_resources()` directly: registration happens before
`lifespan` starts and creates the client.

A lazy reference is needed. Options: a module-level global (worse — implicit
state spanning the whole module), a full-blown DI container (overkill for
this), or this local cell closed over by `create_server`. Went with the third
option: the state is scoped to the factory, and calling `create_server()`
again produces an independent `holder`.

If FastMCP gave resources a context, these three lines would not exist. It is
worth flagging spots like this explicitly in code, so a reader does not go
looking for deep intent that isn't there.

## Assembly order

```python
mcp = FastMCP(name="omniroute", instructions=INSTRUCTIONS, lifespan=lifespan)

register_tools(mcp)
register_resources(mcp, lambda: holder["client"])
register_prompts(mcp)
return mcp
```

`server.py`

Three registration lines are the entire connection between the top layer and
the primitives. Adding a new group of tools = add a module and one line here.

`instructions` (`server.py`) is a description of the server **as a whole**,
which the agent receives at `initialize`. It sits one level above individual
docstrings: a single tool explains itself, while `instructions` explains why
the whole server exists. It also carries the diagnostic hint:

```
If an OmniRoute tool returns an error, start with omniroute_health to
see whether the gateway is reachable.
```

## Two transports

```python
if args.transport == "http":
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    log(f"HTTP transport: http://{args.host}:{args.port}/mcp")
    mcp.run(transport="streamable-http")
else:
    # stdio: not a single character on stdout besides JSON-RPC frames.
    mcp.run(transport="stdio")
```

`server.py`

### stdio — the default

The agent launches the server as a **child process** and talks to it over
stdin/stdout. No port, no socket, no network.

For a backend developer this feels unusual — we are used to a server
listening on a port. But the scheme has real advantages: no authentication is
needed (the process is yours, isolation happens at the OS level), no port
conflicts, the lifecycle is tied to the agent — close the agent, the server
dies. The nearest analogy is CGI, or a plain Unix pipeline.

This is how Claude Code, Claude Desktop, and Cursor work. The config looks
like "a command to run":

```json
{"command": "uv",
 "args": ["--directory", "/path/to/omniroute-mcp", "run", "omniroute-mcp"],
 "env": {"OMNIROUTE_API_KEY": "sk-..."}}
```

### streamable-http — when a subprocess won't do

If the agent lives in a different container or on a different machine, it
cannot spawn a subprocess. Then the server listens on a port and accepts
JSON-RPC on `POST /mcp`. Verified with:

```bash
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'
```

The response arrives as an SSE frame with `serverInfo` and `capabilities`.
The default host is `127.0.0.1`: such a server must never be exposed to the
outside — it has no authentication.

## Mistake #1: `print()` in stdio

This is what everyone trips over the first time they write an MCP server.

In stdio mode, **stdout belongs to the protocol**. JSON-RPC frames go there.
One `print("connecting...")` and the agent gets an invalid frame. The
handshake falls apart, and the diagnostics are unhelpfully vague: "MCP server
failed" with no details.

The fix — all diagnostics go to stderr:

```python
def log(message: str) -> None:
    """Diagnostics strictly go to stderr: stdout belongs to the MCP protocol."""
    print(f"[omniroute-mcp] {message}", file=sys.stderr, flush=True)
```

`server.py`

`file=sys.stderr` is mandatory. `flush=True` makes the message appear
immediately instead of sitting in a buffer (unbuffered output saves a lot of
debugging pain if the process crashes).

Indirect confirmation that the rule is followed: the handshake from chapter 6
succeeds. If even one stray character had leaked into stdout, `initialize`
would not have gone through.

This is also why `masked_key` from chapter 2 matters: the line "connecting to
http://localhost:20128 (key: sk-1a2b3…7f9e)" goes to stderr, which the agent
shows to the user and writes to logs. The full key should never end up there.

## `--selftest` mode

```python
async def selftest(settings: Settings) -> int:
```

`server.py`

A separate mode that calls all eight of the client's methods **directly,
bypassing MCP**, and prints a report:

```
[omniroute-mcp] self-test: http://localhost:20128 (key: sk-1a2b3…7f9e)
[omniroute-mcp]   ✓ health (items/fields: 22)
[omniroute-mcp]   ✓ models (items/fields: dozens)
...
[omniroute-mcp] self-test finished: 8/8 succeeded
```

Why this matters architecturally — **fault localization**. When "the agent
does not see the tools", there are three suspects: the gateway, our server,
the MCP client's config. Selftest separates them: a green selftest clears the
first two, so the problem is in the agent's config.

The same trick as a health check in microservices: a cheap check that saves
hours of investigation. Returns an exit code (`1` on any failure), so it also
works in CI.

Notice a detail: `selftest` builds a list of **coroutines**, then awaits them
in a loop:

```python
checks: list[tuple[str, object]] = [
    ("health", client.health()),
    ...
]
```

`server.py`

The coroutines are created right away, but not executed — as mentioned in
chapter 2, calling an `async def` function only creates an object. Execution
starts at `await` in the loop, meaning the checks run strictly one after
another. That is exactly what's needed here: running them in parallel would
interleave the output and could hit the gateway's rate limits.

## CLI

`argparse` (`server.py`) — standard library, four flags: `--transport`,
`--host`, `--port`, `--selftest`. The entry point is declared in
`pyproject.toml`:

```toml
[project.scripts]
omniroute-mcp = "omniroute_mcp.server:main"
```

This is what makes `uv run omniroute-mcp` work.

## Summary

- A factory instead of a global server — for testability.
- `lifespan` = a DI container: one HTTP client per process, guaranteed cleanup.
- Resources need a lazy reference to the client, hence `holder`.
- stdio (subprocess, default) and streamable-http (network).
- In stdio, stdout is taken by the protocol — logs go to stderr only.
- `--selftest` localizes faults between the gateway, the server, and the
  agent's config.

---

**Next:** [Chapter 6. Tests and verification](06_testing.md) — why the tests
don't need a running OmniRoute, and how the live handshake was checked.
