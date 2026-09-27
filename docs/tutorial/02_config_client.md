# Chapter 2. Configuration and the HTTP client

The two bottom layers. Along the way we'll cover async Python — just enough
to read this code.

## Layer 1: `config.py`

Start with the simple part. The whole module boils down to one immutable
settings object.

```python
@dataclass(frozen=True)
class Settings:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT
```

`src/omniroute_mcp/config.py:20`

`@dataclass` generates the constructor, `__eq__`, and `__repr__` from the
declared fields — the closest analog is a Java record or Lombok's `@Value`.
`frozen=True` makes the object immutable: assigning to a field raises an
exception.

The `from_env()` factory (`config.py:41`) builds settings from the
environment. Two spots are worth a closer look.

**A broken timeout doesn't crash the server:**

```python
try:
    timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
except ValueError:
    timeout = DEFAULT_TIMEOUT
```

An arguable choice — normally I'm in favor of fail-fast. But consider the
launch context: the server is started by the agent as a subprocess, and a
startup crash shows up to the user as a bare "MCP server failed" with no
explanation. A typo in one environment variable shouldn't cost the agent
all eight tools. The critical setting — `api_key` — isn't checked here but
at request time, where the resulting error is far more informative (see
below).

**`base_url` is normalized right away:** `.rstrip("/")` at the point it's
read. A small thing, but it avoids the classic double-slash bug across all
eight client methods. The invariant is enforced once, at the entry point,
instead of being checked on every string join.

### Masking the key

```python
@property
def masked_key(self) -> str:
    if not self.api_key:
        return "<not set>"
    if len(self.api_key) <= 12:
        return "***"
    return f"{self.api_key[:8]}…{self.api_key[-4:]}"
```

`config.py:45`

Why bother with a dedicated method instead of just never logging the key.
Because diagnostic output like "connecting with key sk-1a2b3…7f9e" saves
hours: it immediately shows that a key was actually picked up, and that
it's the right one. The full key must never go to stderr — an agent's logs
end up in bug reports and screenshots.

The `<= 12` branch keeps a short (likely garbage) key from being almost
fully exposed by the `[:8]` and `[-4:]` slices.

## Async Python in five minutes

Async starts here, so a short primer. If you're familiar with Netty,
Node.js, or Project Loom, you already know this model — it's just a matter
of syntax.

**`async def` declares a coroutine.** Calling `client.health()` **does not
run** it — it returns a coroutine object. Nothing happens until it's
awaited. A classic trap: forget the `await`, and the code silently does
nothing.

**`await` yields control to the event loop.** One thread, cooperative
multitasking. While waiting on a network call, the loop runs other tasks.
No thread-per-request, no blocking.

**`async with` is try-with-resources for async resources.** It guarantees
cleanup even if an exception is raised inside.

**`@asynccontextmanager`** turns a generator into such a resource: the code
before `yield` is initialization, the code after `finally` is teardown. The
analog is a bean's `@PostConstruct` / `@PreDestroy` pair. Comes up again in
chapter 5.

Why async at all: an MCP server is, by nature, an I/O-bound proxy. It
spends almost all its time waiting — on the agent, then on OmniRoute. Plus
the protocol SDK itself is async, so there isn't really a choice.

## Layer 2: `client.py`

### Constructor: headers set once

```python
headers = {"Content-Type": "application/json"}
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"
self._http = httpx.AsyncClient(base_url=..., headers=headers, timeout=timeout)
```

`src/omniroute_mcp/client.py:35`

`httpx.AsyncClient` is the async analog of `requests.Session`, with a
connection pool and keep-alive. Headers are set on the client, not on every
request.

Bearer deserves its own note, because it's a non-obvious quirk of
OmniRoute. The gateway has three classes of routes:

| Class | Examples | Requires |
|---|---|---|
| `PUBLIC` | `/api/monitoring/health` | nothing |
| `CLIENT_API` | `/v1/chat/completions`, `/v1/models` | a Bearer key |
| `MANAGEMENT` | `/api/providers`, `/api/usage/*` | a session **or** a Bearer key with `manage` scope |

A key with the `manage` scope covers **all three**. So the client stays
stateless: no cookies, no JWT, no refresh — one header for every request.
This simplification wasn't free: finding it meant reading
`src/server/authz/` in OmniRoute's own source.

### A single point of error handling

`_request()` (`client.py:56`) is the module's heart. All eight public
methods go through it, so the error policy is described exactly once.

Structure: network exceptions first, then status codes, then JSON parsing.

```python
except httpx.ConnectError as exc:
    raise OmniRouteError(
        f"Could not connect to OmniRoute at {self._base_url}. "
        "Check that the gateway is running and that OMNIROUTE_BASE_URL points to the right port."
    ) from exc
```

`client.py:59`

Here's the **main idea of the chapter**, and it's specific to MCP.

In a regular backend, exception text is read by a developer, in logs. Here
it's read by a **language model**, and the text determines whether the
agent can recover on its own. `ConnectError('[Errno 111] Connection
refused')` is useless to an agent. The message above, it can act on: check
whether the gateway is running, or suggest the user fix the port.

Hence the rule: **an error message is also an interface — for the model.**
Write what happened and what to do about it. `from exc` still preserves the
original cause in the traceback, for the human who ends up debugging.

401 is a particularly good example:

```python
if response.status_code == 401:
    raise OmniRouteError(
        "OmniRoute rejected the request: 401 Authentication required. "
        "Set OMNIROUTE_API_KEY to a key with the 'manage' scope "
        "(Dashboard -> API Keys -> enable Management Access)."
    )
```

`client.py:70`

The error includes **a fix, down to the UI path**. This is knowledge I
picked up by reading the gateway's source; it would be a shame to make the
user dig it up again.

### Guarding the context against HTML

```python
def _extract_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200].strip() or "<empty response>"
```

`client.py:187`

An unassuming but important function. OmniRoute is a Next.js app, and on an
unknown path it returns an **HTML 404 page around 460 KB in size**. I ran
into this while exploring the API.

Without truncation, that 460 KB would flow into the exception text → into
the model's context. Result: a blown context window, a token bill, and a
completely useless error message. Hence `[:200]`. There's a dedicated test
for this (chapter 6).

The general principle: **anything that comes from outside and might land in
the context must be size-bounded.** In a regular backend you watch for this
for memory reasons; here it's about cost and the model's attention.

### The method that caught a real bug

```python
async def models(self) -> list[dict[str, Any]]:
    """...
        /api/models -> 30 models, prefixes shortened (`cl/`, `kr/`, `oc/`),
                        no `auto/*` pseudo-models at all;
        /v1/models  -> 125 models, including `auto/cheap`, `auto/best-coding`.
    """
    data = await self._get("/v1/models")
    return data.get("data", [])
```

`client.py:106`

This one's worth telling as a story, because it's a textbook integration
mistake.

Originally I used the management endpoint `/api/models` — made sense, since
we already hit `/api/*` for providers and quotas. Comparing two live
responses showed the endpoints return **different namespaces**: 30 models
vs 125, shortened prefixes vs full ones, and — the important part —
`/api/models` has no `auto/*` pseudo-models at all.

And `omniroute_chat` hits `/v1/chat/completions`. So the agent would get a
catalog from one namespace and call into another: the names wouldn't be
recognized, and it would never learn about `auto/*` — the most convenient
entry point for an agent.

**Rule:** the list of "what can be called" must come from the same API
surface as the call itself. This wasn't caught by documentation — it was
caught by comparing two live responses.

### Two traps of a live gateway

```python
payload: dict[str, Any] = {
    "model": model,
    "messages": messages,
    "max_tokens": max_tokens,
    "stream": False,
}
if temperature is not None:
    payload["temperature"] = temperature
```

`client.py:182`

**`"stream": False` is hardcoded.** Without it the gateway responds with
`text/event-stream` (the key's mode is `streamDefaultMode: "legacy"`), and
`response.json()` fails. We don't need streaming: the MCP tool returns the
whole result at once anyway.

**`temperature` is added only if it's set.** `None` must not be sent:
models have different defaults, and an explicit `null` might be read as
"zero". The classic optional-field pattern — don't conflate "not
specified" with "specified as empty".

And a third thing visible in the signature: `max_tokens: int = 1024`. A
deliberately generous default, because with `max_tokens=20` the gateway
returned `502: reasoning consumed 19/20 tokens — no content output`. For
reasoning models, hidden reasoning eats the budget before any output
starts.

Both traps came from probing a live instance — neither is in the
documentation. The takeaway for integrations: **capture the contract from
the running service, not from the docs.**

---

**Next:** [Chapter 3. Formatting](03_formatting.md) — a layer with no
equivalent in a regular backend, and why it determines the quality of an MCP
server.
