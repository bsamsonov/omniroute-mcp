# Chapter 4. MCP primitives: tools, resources, prompts

Three layers in one go — they are small. The real content of this chapter is
not the code itself, but how a language model reads that code.

## The schema is derived from the function signature

Let's start with the most pleasant part of FastMCP. Here is the full code of
one tool:

```python
@mcp.tool()
async def omniroute_list_models(ctx: Context, filter: str = "") -> str:
    """Show the models available through OmniRoute.

    Call this before omniroute_chat if you are unsure of the model
    name.
    ...
    Args:
        filter: optional substring to filter by (e.g. "claude", "gpt").
    """
    return formatting.format_models(await _client(ctx).models(), filter)
```

`src/omniroute_mcp/tools.py`

There is no schema file anywhere. FastMCP builds a JSON Schema from the
**type annotations** via reflection, and takes the description for the model
from the **docstring**. This is confirmed by a live `tools/list` call:

```
omniroute_list_models  params=['filter'] required=[]
omniroute_chat  params=['prompt','model','system','max_tokens','temperature'] required=['prompt']
```

`filter` has a default — so it is not in `required`. `omniroute_chat` has only
`prompt` without a default, so it ends up in `required`. The contract for the
model and the function signature are one and the same source of truth.

If you are used to Spring annotations, this is the same trick: `@mcp.tool()`
plays the role of `@GetMapping`, and parameter types play the role of
`@RequestParam`. A decorator in Python is just a function that takes a
function and registers it.

## The docstring is a prompt

Here is the main difference between MCP and REST, and it is worth dwelling on.

In REST, the endpoint description is read by a developer. Get it wrong, and
the compiler catches it, or a test fails. In MCP, the tool description is read
by a **language model**, and that is the **only** thing it uses to decide
whether to call the tool, and with which arguments.

So the docstring is not documentation — it is **part of the prompt**. It
should be written accordingly: not "how it works", but **"when to use it"**.

Compare. Bad (describes the implementation):

```
Makes a GET request to /api/monitoring/health and returns a
formatted response.
```

What the project actually has (`tools.py`):

```
Check the status of the OmniRoute gateway.

Call this first if any other OmniRoute tool unexpectedly fails —
it shows whether the gateway is alive, its version, uptime,
circuit breaker states, and any problem providers.
```

The second version contains a **trigger condition**: "if any other tool
fails". This changes the agent's behavior — instead of blindly retrying, it
goes to check the gateway first.

The same trick appears in `omniroute_list_models`: "Call this before
`omniroute_chat` if you are unsure of the model name." We chained two tools
together without writing a single line of code. **Docstrings define the
transition graph between tools.**

And a warning right inside a parameter's description (`tools.py`):

```
max_tokens: response token limit. Do not go below ~512:
    for reasoning models, hidden reasoning eats into the
    budget and the answer can come back empty.
```

A lesson learned through debugging (the `502` from chapter 2) is placed
exactly where the model will read it — **before** it steps on the same rake.

## Why there are 8 tools, not 104

OmniRoute itself ships with a built-in TypeScript MCP server exposing **104
tools**. Ours exposes 8. That is not laziness — it is a deliberate
architectural decision, and it matters more here than the code itself.

The tool catalog is sent into the context **on every request to the model**.
104 tools with descriptions are kilobytes that:

- eat into the context window before any work has started;
- force the model to choose among 104 similar options, which sharply hurts
  accuracy;
- include dozens of tools that a given task will never need.

This is exactly the same trade-off as API design in general: **a wide
interface is convenient for the author and inconvenient for the consumer.**
Here the consumer is a model with limited attention, and the cost of a wide
interface is higher than usual.

The eight tools were chosen by the principle "the agent actually needs this",
not "the API has this". One does the work (`omniroute_chat`), seven provide
observability.

## How a tool gets its client

```python
def _client(ctx: Context) -> OmniRouteClient:
    return ctx.request_context.lifespan_context.client
```

`tools.py`

`Context` is what FastMCP hands to a tool if it declares a parameter of that
type (note: `ctx` does **not** end up in the schema shown to the model —
FastMCP filters it out). Inside it is `lifespan_context`, an object created
when the server starts.

This is familiar **DI**: the dependency is not created inside the handler, it
is supplied from outside, from a container with a managed lifecycle. Like
`@Autowired` on a singleton injected into a controller. We cover the container
implementation itself in chapter 5.

Why this matters: creating an `httpx.AsyncClient` inside every tool is a
common and expensive mistake. Every call would mean a new TCP handshake
instead of reusing a keep-alive connection.

## Registration through a function, not module-level decorators

```python
def register_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def omniroute_health(ctx: Context) -> str:
        ...
```

`tools.py`

Tools are defined **inside** the registration function, not at module level.
Reason: the `@mcp.tool()` decorator binds a function to a **specific server
instance**. If the decorators lived at module level, they would need a global
`mcp` — and global state gets in the way of assembling a second server with
different settings in a test.

The price is nesting, and the fact that the functions are not directly
visible from outside the module. That is fine for testing: tools are tested
through an MCP session, and the logic worth testing directly lives in the
formatters.

## Error handling: there isn't any — and that is correct

None of the tools are wrapped in `try/except`. To a backend developer this
looks unfinished, so an explanation is left right in the code (`tools.py`):

FastMCP itself catches the exception and returns it to the agent as a result
with the `isError` flag set. Confirmed with a live call without a key:

```
isError: True
Error executing tool omniroute_list_providers: OmniRoute rejected the request:
401 Authentication required. Provide OMNIROUTE_API_KEY with a key that has the 'manage' scope…
```

The agent **receives the error text as a regular response** and can react to
it. Catching the exception just to return "an error occurred" would erase
useful information.

This is where it becomes clear why chapter 2 spent so much effort on the
wording of `OmniRouteError`: it goes straight to the model, with no
intermediate layer. The exception is part of the user interface.

## Validation at the boundary

```python
limit = max(1, min(limit, 100))
```

`tools.py` (and `hours = max(1, min(hours, 168))` further down)

The schema lets the model pass any integer. The model may well request
`limit=10000` — it does not know about the gateway's own limits (`hours` is
only valid in the 1..168 range in OmniRoute).

We **clamp** the value instead of raising an error. Rationale: the agent's
goal is "look at recent calls", and that goal is satisfied by `limit=100`.
Rejecting the call would waste an extra turn just to fix the argument.

General principle: **don't trust arguments coming from the model.** It
generates them probabilistically, not against a spec. This is your usual
"don't trust user input", except the user is now an LLM.

## Resources: data instead of actions

```python
@mcp.resource("omniroute://health", mime_type="text/plain")
async def health_resource() -> str:
    client: OmniRouteClient = client_factory()
    return formatting.format_health(await client.health())
```

`src/omniroute_mcp/resources.py`

The body almost matches the `omniroute_health` tool — the duplication is
deliberate, so both primitives can be shown on the same data.

The difference is practical, not cosmetic:

| | tool | resource |
|---|---|---|
| Initiator | the model, on its own decision | the client/user |
| Addressing | by name + arguments | by URI |
| When it enters the context | after a model turn | up front, before it starts working |
| Side effects | allowed | none, read-only |

A user can attach a resource in the UI like a file — the context appears
**before** the model starts working, without spending a model turn on a tool
call.

Rule of thumb: "the agent decides when it is needed" → tool. "Reference
context that might be needed upfront" → resource.

### Dependency injection through a closure

Unlike tools, resources **do not receive a `Context`**. The client is passed
in via a factory:

```python
def register_resources(mcp: FastMCP, client_factory) -> None:
```

`resources.py`

It is called as `register_resources(mcp, lambda: holder["client"])`
(`server.py`). The lambda resolves the client **lazily**, at the moment the
resource is read, once `lifespan` has already created it. The client itself
cannot be passed in directly — it does not exist yet at registration time.

In your own terms: a provider instead of an instance, `ObjectFactory` instead
of a bean. Lazy dependency resolution using the simplest possible mechanism —
no DI framework needed.

## Prompts: scripts for a human

The third primitive, and the most underrated one.

```python
@mcp.prompt()
def diagnose_gateway() -> str:
    """Full diagnostics for the OmniRoute gateway: what is broken and why."""
    return (
        "Run diagnostics on the OmniRoute gateway. Proceed in order:\n\n"
        "1. Call omniroute_health — assess overall status and circuit breakers.\n"
        "2. Call omniroute_list_providers — look for inactive connections, ...\n"
        ...
    )
```

`src/omniroute_mcp/prompts.py`

A prompt returns **task text**, it does not do the work itself. In Claude
Code it shows up as the command `/mcp__omniroute__diagnose_gateway`.

Why this matters architecturally: tools give **capabilities**, a prompt gives
a **method** for applying them. The order of diagnostic steps is expert
knowledge; without a prompt, it would have to be re-explained to the agent
every time.

Notice the last line of the task: "Do not just restate the raw tool output —
synthesize it." A direct pushback against a model's typical behavior of
happily dumping everything it received.

The second prompt, `compare_models(task)` (`prompts.py`), takes an argument —
FastMCP turns function parameters into prompt arguments exactly the same way
it did for tools.

## Summary

- A tool's schema = the function signature; we never write separate schemas.
- The docstring is part of the prompt. Write "when to use it", not "how it works".
- Docstrings link tools into chains.
- Fewer tools → more accurate selection. 8 beats 104.
- We don't catch exceptions: FastMCP hands them to the agent as `isError`.
- Arguments coming from the model get clamped into a valid range.
- tool / resource / prompt differ by initiator: model / client / human.

---

**Next:** [Chapter 5. Server and transports](05_server_transports.md) — how
everything is assembled, what lifespan is, and why you can't `print()` in
stdio.
