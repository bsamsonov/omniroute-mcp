# Chapter 1. Architecture

Let's look at what the server is made of, why there are exactly seven
layers, and how a single tool call flows through them. The following
chapters go through the layers bottom-up.

## The dependency rule

The whole project rests on one rule:

```
server → tools / resources / prompts → formatting → client → config
```

Dependencies point **only left to right**. No lower layer knows about the
layers above it.

If you know hexagonal architecture or Clean Architecture, this is the same
idea in miniature, and it buys two concrete benefits:

**`client.py` knows nothing about MCP.** It imports nothing from `mcp`. It's
a plain async HTTP client — you could pull it out and use it in a script, a
FastAPI app, or a notebook. MCP turns out to be a thin wrapper around
portable code, not an architectural dictate.

**`tools.py` knows nothing about HTTP.** It has no idea that OmniRoute is
REST. It operates on the client's methods. If the transport to the gateway
changes (gRPC, a unix socket), the tools won't notice.

You can check the rule with grep, and it's an honest architecture test:

```bash
grep -l "import httpx" src/omniroute_mcp/*.py   # only client.py
grep -l "from mcp"    src/omniroute_mcp/*.py    # only tools/resources/prompts/server
```

## Why split it up if it's only 1000 lines

A fair question — at this size, you could write it as one file. Three
reasons this separation is justified here.

**A teaching reason.** The project is teaching material for a lecture. One
file, one idea. Formatting, pulled into its own module, physically forces
you to notice: "there's a separate concern here — deciding what makes it
into the model's context." In a monolith that insight would dissolve among
the HTTP calls.

**A testing reason.** `formatting.py` consists of pure functions: a dict in,
a string out. They're tested without a network, without mocks, without
async — just a plain call. If formatting were smeared across the tools,
checking every output line would require spinning up an MCP session. In
chapter 6 you'll see that 13 of 22 tests are exactly this kind, and they run
in milliseconds.

**An operational reason.** Knowledge of the external API is collected in one
place. OmniRoute is a living project; its endpoints change between
versions. When something breaks, you fix `client.py`, not go hunting for
URLs across eight tools.

## The layers, one by one

### Layer 1 — `config.py`

The only module that reads environment variables. It returns an immutable
`Settings` object (`src/omniroute_mcp/config.py:32`).

Why the environment and not a config file or CLI flags: in stdio mode, the
MCP server is started by the agent as a **child process**. There's no
interactive dialog and no CLI available. Environment variables are the only
practical channel; the agent forwards them from its own configuration.

### Layer 2 — `client.py`

An async HTTP client for OmniRoute (`src/omniroute_mcp/client.py:28`). Eight
methods for confirmed endpoints, plus a single point of error handling.

This is also where the **anti-corruption layer** lives: httpx's low-level
exceptions get translated into `OmniRouteError` with text a language model
can understand. Covered in chapter 2.

### Layer 3 — `formatting.py`

Pure functions that turn "gateway JSON" into compact text. The least
obvious layer for a backend developer: a regular REST service has no such
thing, because there the response is parsed by a program. Here it's read by
a model, and size starts costing money. Chapter 3.

### Layers 4-6 — `tools.py`, `resources.py`, `prompts.py`

The three MCP primitives, one file each. Thin: a tool is usually two lines —
call the client, hand the result to the formatter. Its real value is in the
docstring, which the model reads. Chapter 4.

### Layer 7 — `server.py`

Assembly. Creates settings, brings up the client in `lifespan`, registers
the primitives, picks the transport. Plus a `--selftest` mode. Chapter 5.

## Tracing a call

Let's trace `omniroute_health` from the agent to the gateway. It's the
shortest path through all the layers.

**1. The agent sends a JSON-RPC** message to the process's stdin:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call",
 "params":{"name":"omniroute_health","arguments":{}}}
```

**2. FastMCP** (the library) parses the frame, finds the registered tool,
and calls the Python function.

**3. The tool** at `src/omniroute_mcp/tools.py:51` — a one-line body:

```python
return formatting.format_health(await _client(ctx).health())
```

Read it right to left: get the client from the context → ask for health →
format the result.

**4. `_client(ctx)`** (`src/omniroute_mcp/tools.py:30`) fetches the shared
HTTP client from the lifespan context. It doesn't create a new one — it
reuses the one brought up at startup.

**5. The client** at `client.py:102` does a `GET /api/monitoring/health`,
catches network errors and statuses, and returns parsed JSON — 22 top-level
fields, ~1.2 KB.

**6. The formatter** at `formatting.py:49` compresses those 22 fields down
to six lines of text.

**7. FastMCP** wraps the string in a JSON-RPC response and writes it to
stdout.

The agent receives:

```
OmniRoute: healthy
Version: 3.8.42
Uptime: 1d 1h
Active connections: 6
Providers: 4 active out of 5 configured (catalog: 242)
Circuit breakers: 6 closed, 1 degraded, 0 open
Problem providers:
  ⚠ cline: DEGRADED (failures: 6)
```

Notice step 6 — that's where 1.2 KB of JSON turns into 300 bytes of text. A
regular backend has no such step: you'd hand back the JSON as is, and the
client would parse it. Why it's different here — chapter 3.

## What's deliberately missing

Two things this architecture **doesn't have**, on purpose.

**No model/DTO layer.** OmniRoute's responses are passed around as
`dict[str, Any]`. To a backend developer used to DTOs and validation this
looks sloppy, and in a large project I wouldn't do it this way. Here it's a
deliberate trade-off: OmniRoute returns large objects with dozens of
fields, of which we use 5-10. Writing full schemas would mean dozens of
lines per model just to validate data we throw away in the formatter
anyway. The price: a typo in a key name isn't caught statically. We
compensate with formatter tests run against real gateway responses
(chapter 6).

**No caching of its own.** Also deliberate: the agent asks for health to
learn the state **right now**. A cached health check is worse than no
health check at all. Caching is OmniRoute's own job — there it's semantic
and configurable.

---

**Next:** [Chapter 2. Configuration and client](02_config_client.md) — async
Python in plain terms, and why error text is written for the model, not the
developer.
