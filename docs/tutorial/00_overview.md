# Chapter 0. What the project does and how to read this walkthrough

## Who this is for

You know backend development: layers, DI, connection pools, adapters,
contracts, tests against mocks. You don't need to already know the two
things this project is built on — the **MCP protocol** and **async
Python**. Both are explained through analogies you already know, not from
first principles.

Read the chapters in order — each one refers back to the previous ones. The
project's entire codebase is roughly 1000 lines, so this walkthrough
genuinely covers all of it, not just "the important parts".

## The problem the project solves

You have **OmniRoute** running locally (`http://localhost:20128`) — a
gateway to LLMs. In essence, it's an API gateway for language models: behind
one endpoint sit dozens of models from different providers, with routing,
fallbacks, circuit breakers, quotas, and token accounting. A familiar
architectural role — just with models instead of microservices behind it.

The problem: an **AI agent** can't take advantage of any of this. An agent
(Claude Code, Cursor, your own SDK-based agent) knows how to call tools, but
it knows nothing about your local OmniRoute instance. It needs an adapter.

This project is that adapter:

```
agent (Claude Code / Cursor / your own SDK agent)
        │  MCP: JSON-RPC over stdio or HTTP
        ▼
omniroute-mcp        ← this project, ~1000 lines of Python
        │  REST + Bearer key
        ▼
OmniRoute  http://localhost:20128
        │
        ▼
dozens of models: Anthropic, OpenAI, Moonshot, DeepSeek, …
```

The project was written as **teaching material for a lecture** on autonomous
agents. That explains the coding style: each file illustrates one idea, and
comments explain intent rather than restate syntax.

## What MCP is, for a backend developer

**MCP (Model Context Protocol)** is the protocol an LLM agent uses to talk
to external tools. Three points to fit it into a familiar mental model.

**1. It's JSON-RPC, nothing exotic.** Plain JSON-RPC 2.0: `initialize`,
`tools/list`, `tools/call`. No magic.

**2. Discovery happens at runtime.** This is the key difference from the
REST integrations you're used to. The agent **doesn't know in advance** what
tools you have. It connects, calls `tools/list`, and gets back a catalog
with a JSON Schema for each tool. The closest analogy is WSDL or OpenAPI —
except it's fetched on every startup instead of being compiled into the
client.

The practical consequence, which is the whole point of the protocol: **a new
MCP server means new agent capabilities with zero changes to the agent's
code.** Think service discovery, but for LLM capabilities.

**3. The consumer of the contract is a model, not a program.** This is what
breaks habits the most. In REST, a developer reads the endpoint description,
and the compiler and code enforce correctness of the call. In MCP, the tool
description is read by a **language model**, which decides — based on the
text — whether to call it, and with what arguments.

That means a docstring stops being documentation and becomes **part of the
prompt**. Describe a tool poorly, and the agent won't call it, or will call
it at the wrong time. No compiler can save you here. We'll come back to this
in chapter 4 — it's a core idea of the whole project.

## Three primitives of the protocol

An MCP server can expose three different kinds of things. The difference
isn't cosmetic — each has a different caller:

| Primitive | Who initiates | Analogy | Purpose |
|---|---|---|---|
| **tool** | the model, on its own | an RPC method | an action, may change the world |
| **resource** | the client/user | GET on a resource URI | reference context |
| **prompt** | a human | a template / slash command | a usage scenario for the tools |

Our server exposes all three: 8 tools, 2 resources, 2 prompts — deliberately,
so that a single project demonstrates each one.

## What the server can do

Eight tools. One is the centerpiece; the rest serve observability:

| Tool | Purpose |
|---|---|
| `omniroute_chat` | **the core**: send a request to any LLM through routing |
| `omniroute_health` | gateway status: version, uptime, circuit breakers |
| `omniroute_list_models` | model catalog, with a filter |
| `omniroute_list_providers` | connections, auth, errors |
| `omniroute_quota` | remaining quota, token status |
| `omniroute_provider_stats` | success rate, latency |
| `omniroute_recent_calls` | most recent calls through the gateway |
| `omniroute_usage_summary` | an N-hour aggregate |

Why would an agent need `omniroute_chat` if it's a language model itself?
For **delegation**. An expensive model hands off bulk grunt work to a cheap
one: "summarize this log with `auto/cheap`, and I'll work from the digest."
This saves money and — more importantly — the primary agent's context
window.

## Map of the code

Seven modules, strictly layered. Full breakdown in chapter 1; here's just
the orientation table:

| File | Layer | Role |
|---|---|---|
| `config.py` | 1 | settings from the environment |
| `client.py` | 2 | OmniRoute HTTP client; knows nothing about MCP |
| `formatting.py` | 3 | JSON → compact text for the model |
| `tools.py` | 4 | MCP tools |
| `resources.py` | 5 | MCP resources |
| `prompts.py` | 6 | MCP prompts |
| `server.py` | 7 | assembly, lifespan, transport |

Plus `tests/` (35 tests against mocks) and `src/omniroute_agent/discovery.py`
— the flip side of the coin: how the agent connects to the server.

## Walkthrough plan

1. **Architecture** — seven layers, the dependency rule, tracing a request.
2. **Configuration and client** — async Python for a backend developer,
   translating errors.
3. **Formatting** — the least obvious layer: context hygiene.
4. **MCP primitives** — tools/resources/prompts, a schema derived from a
   function signature.
5. **Server and transports** — assembly, lifespan, stdio vs HTTP.
6. **Tests and verification** — mocks, selftest, a live handshake.
7. **Glossary** — terminology and where to find things.

---

**Next:** [Chapter 1. Architecture](01_architecture.md) — what layers the
server is made of, and why dependencies only go one way.
