# omniroute-mcp

A teaching MCP server and autonomous agent in Python, using the [OmniRoute](https://github.com/diegosouzapw/OmniRoute) LLM gateway as a real backend.

[![tests](https://github.com/bsamsonov/omniroute-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/bsamsonov/omniroute-mcp/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Why this project

OmniRoute already ships its own [built-in MCP server](https://github.com/diegosouzapw/OmniRoute/wiki/MCP-Server) with 100+ tools. This repo is **not a replacement** for it. It is a compact (~1500 lines of code), fully tested reference implementation that shows all three MCP primitives (tools, resources, prompts), both transports (stdio and streamable HTTP), a bare MCP client (discovery), and an LLM tool-calling agent loop — each file illustrates one idea, and the whole thing reads in one sitting.

## What this demonstrates

- A tool's JSON Schema derived straight from a Python function signature (no separate schema file)
- Docstrings written as prompts for the model, not documentation for humans
- A dedicated formatting layer for context hygiene (raw API JSON is too verbose for an LLM)
- A deliberately small tool catalog (8 tools) instead of a large one, for better tool-selection accuracy
- Errors written for the model to read and react to, not just for a developer to debug
- The stdio rule that trips up most first-time MCP server authors: stdout belongs to the protocol
- All three MCP primitives side by side: tool (model-initiated), resource (client-initiated), prompt (human-initiated)
- A minimal autonomous agent loop: list tools -> call the model -> call a tool -> feed the result back -> repeat
- The same agent logic running unchanged over two different transports (stdio and HTTP)

```
agent (Claude Code / Cursor / your own SDK-based agent)
        │  MCP, JSON-RPC over stdio or HTTP
        ▼
omniroute-mcp  ──── 8 tools · 2 resources · 2 prompts
        │  REST, Bearer key
        ▼
OmniRoute  http://localhost:20128
        │
        ▼
dozens of models: Anthropic, OpenAI, Moonshot, DeepSeek, …
```

Tested against OmniRoute v3.8.x.

---

## Quick start

Prerequisites: Python 3.10+, [uv](https://docs.astral.sh/uv/), and a running
OmniRoute instance (default `http://localhost:20128`).

```bash
git clone https://github.com/bsamsonov/omniroute-mcp.git
cd omniroute-mcp
uv sync --extra dev
uv run pytest -q   # 35 tests, no gateway needed
```

### 1. Get an OmniRoute API key

You need a key with the **`manage`** scope — without it, management endpoints return `401`.

Via the dashboard: `http://localhost:20128` → **API Keys** → *Create* → enable **Management Access**.

Or via the API:

```bash
# 1) log in with the dashboard password and save the cookie
curl -s -c /tmp/or.txt -X POST http://localhost:20128/api/auth/login \
  -H 'Content-Type: application/json' -d '{"password":"YOUR_PASSWORD"}'

# 2) issue a key
curl -s -b /tmp/or.txt -X POST http://localhost:20128/api/keys \
  -H 'Content-Type: application/json' \
  -d '{"name":"mcp-server","scopes":["manage","read:health","read:models","read:usage","execute:completions"]}'
```

### 2. Configure the environment

```bash
cp .env.example .env
# fill in OMNIROUTE_API_KEY, CLIENT_MODEL, and OMNIROUTE_BASE_URL if the port differs
```

`.env` is read by both halves of the project — the server and the agent. Real
environment variables take precedence over the file: the host agent's config
(the `env` section in `mcpServers`) always overrides `.env`.

`CLIENT_MODEL` is required: there is no default in the code, on purpose.
Specify a concrete model rather than the `auto/*` alias — the gateway resolves
aliases to a provider on the fly and sometimes returns `400 Invalid model`.

### 3. Check the connection to the gateway

```bash
uv run omniroute-mcp --selftest
```

Expected output — all eight checks green:

```
[omniroute-mcp] self-test: http://localhost:20128 (key: sk-1a2b3…7f9e)
[omniroute-mcp]   ✓ health (items/fields: 22)
[omniroute-mcp]   ✓ models (items/fields: 47)
...
[omniroute-mcp] self-test finished: 8/8 succeeded
```

`--selftest` talks to OmniRoute directly, bypassing MCP. If it is green but
the agent does not see the tools, the problem is in the MCP client's
configuration, not in the gateway.

### 4. Connect it to an agent

**Claude Code** — one command:

```bash
claude mcp add omniroute \
  --env OMNIROUTE_BASE_URL=http://localhost:20128 \
  --env OMNIROUTE_API_KEY=sk-... \
  -- uv --directory /absolute/path/to/omniroute-mcp run omniroute-mcp
```

**Claude Desktop / Cursor** — in the MCP client config:

```json
{
  "mcpServers": {
    "omniroute": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/omniroute-mcp", "run", "omniroute-mcp"],
      "env": {
        "OMNIROUTE_BASE_URL": "http://localhost:20128",
        "OMNIROUTE_API_KEY": "sk-..."
      }
    }
  }
}
```

**Agent in a container or on another machine** — run the network version:

```bash
uv run omniroute-mcp-http --port 8765
# endpoint: http://127.0.0.1:8765/mcp
```

---

## Two versions of the server

The same server, the same 8 tools. Only the transport differs:

| | Local (stdio) | Network (HTTP) |
|---|---|---|
| Command | `omniroute-mcp` | `omniroute-mcp-http` |
| Who launches it | the agent itself, as a subprocess | you, ahead of time |
| Lifetime | dies with the agent | runs independently |
| How many agents | one per process | several per server |
| Authentication | not needed (OS-level isolation) | **none — do not expose publicly** |
| When to use | Claude Code, Cursor, Desktop | agent in a container or over the network |

The server code is shared: only `run_stdio()` and `run_http()`
(`src/omniroute_mcp/server.py:166`) differ.

## Agent (the client half)

The `omniroute_agent` package is not a sample — it is as much a working part
of the project as the server. Details in [`docs/agent.md`](docs/agent.md).

```bash
# autonomous agent over the local version (no need to start the server separately)
uv run omniroute-agent

# your own task instead of the default diagnostic
uv run omniroute-agent "Which providers are failing and why?"

# over the network version: start the server first, then the agent
uv run omniroute-mcp-http --port 8765
uv run omniroute-agent-http

# a bare MCP client without an LLM: handshake, discovery, manual tool call
uv run omniroute-discover
```

The "brain" model comes from `CLIENT_MODEL` in `.env`; override it once with
the `--model` flag.

The agent is a loop wrapped around a model call: the LLM asks to call a tool,
we call it through MCP, feed the result back, and repeat. The logic lives in
`src/omniroute_agent/core.py` and is **the same for both versions** — only
the way the session is created differs.

---

## What the server can do

### Tools (called by the model)

| Tool | Purpose |
|---|---|
| `omniroute_chat` | **Core.** Send a request to any LLM through OmniRoute's routing |
| `omniroute_health` | Gateway status: version, uptime, circuit breakers |
| `omniroute_list_models` | Model catalog (+ substring filter) |
| `omniroute_list_providers` | Connections, authentication, token expiry, errors |
| `omniroute_quota` | Remaining quota and token status |
| `omniroute_provider_stats` | Request volume, success rate, latency |
| `omniroute_recent_calls` | Most recent requests through the gateway |
| `omniroute_usage_summary` | Aggregate over N hours: requests, tokens, errors |

### Resources (loaded by the client)

`omniroute://health` · `omniroute://models`

### Prompts (launched by a human)

`diagnose_gateway` — a step-by-step gateway diagnostic.
`compare_models` — run a task through several models and compare.

### Example: delegating a subtask

The key scenario: an agent hands off a large, cheap task to another model,
saving its own context and money:

> Summarize this log with `omniroute_chat` using a cheap model, and
> only work through the summary yourself.

If the `model` parameter is omitted, the tool falls back to `CLIENT_MODEL`
from `.env`.

---

## Tutorial

A detailed source-by-source walkthrough is in [`docs/tutorial/`](docs/tutorial/README.md):
eight chapters, from an MCP overview to autonomous agents.

## Code structure

Two installable packages: the server and the client that talks to it.

```
src/omniroute_mcp/     server: 8 tools, 2 resources, 2 prompts
src/omniroute_agent/   client: an autonomous agent on top of the server
```

Inside the server, dependencies point strictly one way:
`server → tools/resources/prompts → formatting → client → config`.
No lower layer knows about the layers above it.

| File | Layer | Idea |
|---|---|---|
| `envfile.py` | 0 | Finds and parses `.env`; environment variables take precedence over the file |
| `config.py` | 1 | Settings from the environment; secret masking |
| `client.py` | 2 | The only module that knows OmniRoute's REST API. Knows nothing about MCP |
| `formatting.py` | 3 | JSON → compact text. Context hygiene |
| `tools.py` | 4 | Tools. Schema is derived from the function signature |
| `resources.py` | 5 | Resources. Data instead of actions |
| `prompts.py` | 6 | Prompts. Usage scenarios for the tools |
| `server.py` | 7 | Assembly, lifespan, transport selection |

The client mirrors the same separation: `core.py` is the agent's logic,
transport-agnostic; `stdio.py` and `http.py` are just the way the session is
created; `discovery.py` is a bare MCP client with no LLM (run it with
`uv run omniroute-discover`, walkthrough in [`docs/agent.md`](docs/agent.md)).

---

## Seven things this code shows in a live example

**1. A tool's schema is its function signature.**
There is no separate schema file. FastMCP builds the JSON Schema from type
annotations, and the description shown to the model comes from the docstring.

**2. The docstring is a prompt, not documentation.**
It is read by the LLM, and it is the only thing the model uses to decide
whether to call the tool. Write *when to use it*, not *how it works*.

**3. Context hygiene matters more than API completeness.**
`/api/monitoring/health` returns 22 fields and ~1.2 KB, including
`memoryUsage.arrayBuffers`. The agent needs six lines. You pay for the extra
tokens on every call, and they dilute the model's attention. Hence the
separate `formatting.py` layer.

**4. Fewer tools mean better selection.**
OmniRoute's built-in MCP server exposes 104 tools. This one has 8. A bloated
catalog eats context and hurts selection accuracy; 8 well-described tools get
used correctly by the agent.

**5. Errors are written for the model, not the developer.**
A bare `ConnectError` is useless to the agent. "Could not connect to
OmniRoute at …, check that the gateway is running" lets the agent recover on
its own. Tools are deliberately not wrapped in `try/except`: FastMCP catches
the exception and returns it as a result with the `isError` flag set.

**6. In stdio transport, stdout belongs to the protocol.**
One stray `print()` breaks the JSON-RPC handshake. All diagnostics go to
stderr (the `log()` function). This is the #1 mistake for anyone writing
their first MCP server.

**7. Three primitives, three different jobs.**

| Primitive | Who triggers it | Purpose |
|---|---|---|
| tool | the model | action |
| resource | the client | context |
| prompt | a human | scenario |

### Two pitfalls found on a live OmniRoute

Both were discovered against a running gateway and handled in `client.py` — a
good example of why an integration is validated against the real service
rather than written from documentation alone:

1. **`stream: false` is mandatory.** Otherwise the gateway responds with
   `text/event-stream` (for keys with `streamDefaultMode: "legacy"`), and JSON
   parsing fails.
2. **`max_tokens` needs headroom.** With `max_tokens: 20` the gateway
   returned `502`: `reasoning consumed 19/20 tokens — no content output` —
   for reasoning models, hidden reasoning eats into the token budget.

---

## Tests

```bash
uv run pytest -v
```

Tests mock HTTP (`respx`) and **do not require a running OmniRoute** — CI does
not depend on any external infrastructure.

## Requirements

Python ≥ 3.10 · [uv](https://docs.astral.sh/uv/) · a running OmniRoute instance

## Security

The key lives only in `.env` (which is gitignored) or in the environment
variables of the MCP client's config. It is masked in logs and diagnostics
(`config.Settings.masked_key`). By default, the server listens on `127.0.0.1`.

## License

MIT, see [LICENSE](LICENSE).
