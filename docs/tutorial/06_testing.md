# Chapter 6. Tests and verification

35 tests, none of them need a running OmniRoute. Plus three levels of
verification against the live system. Let's break down what checks what, and
why the split is exactly this way.

## The verification pyramid

The project has three levels, and each answers its own question:

| Level | Run with | Needs OmniRoute | Answers |
|---|---|---|---|
| Unit tests | `uv run pytest` | no | does our code work correctly |
| Selftest | `omniroute-mcp --selftest` | yes | is the gateway reachable, are the endpoints alive |
| MCP handshake | `src/omniroute_agent/discovery.py` | yes | does an agent see the whole server |

The logic behind the split is the same as in microservices: fast, isolated
tests in CI, smoke checks against the live system by hand at deploy time.

## Why tests don't hit the live gateway

The temptation is real: OmniRoute is running locally anyway, so the tests
could be three lines each. The reasons not to do that are familiar, but here
they are sharper.

**Failures cannot be reproduced on demand.** How do you make the live gateway
return a 401? Take away the key. A 460 KB HTML page? A `ConnectError`? These
are exactly the paths that need testing — they are harder than the happy path,
and bugs live there.

**Tests become slow.** One live `chat_completion` takes 5 to 10 seconds (you
can see this in the call logs from chapter 3). 22 such tests would turn a run
into a three-minute wait, and you would stop running them.

**Tests become non-deterministic.** The model answers differently every time,
routing picks different providers. You cannot assert against that.

The solution is `respx`, a library that replaces httpx's transport. Requests
never go out over the network; they are matched against rules and given
canned responses. The analog is WireMock or MockWebServer.

## Client tests

### Checking the contract with the gateway

```python
@respx.mock
async def test_bearer_header_is_sent(client: OmniRouteClient) -> None:
    route = respx.get(f"{BASE}/api/monitoring/health").mock(
        return_value=httpx.Response(200, json={"status": "healthy"})
    )
    await client.health()
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-test-key"
```

`tests/test_client.py`

This checks not the response, but the **outgoing request**. `respx` gives
access to intercepted calls, and we assert that the header went out in the
right format. Forget the Bearer prefix, and every management endpoint would
return 401 — this test catches that.

### A regression for a real bug

```python
async def test_chat_forces_stream_false(client: OmniRouteClient) -> None:
    """Regression test for a real gateway trap.

    Without stream=false, OmniRoute responds with text/event-stream, and JSON parsing fails.
    """
    ...
    assert _sent_body(route)["stream"] is False
```

`tests/test_client.py`

A test for the trap from chapter 2. Someone (me, six months from now,
included) will want to "clean up" and remove the hardcoded `stream: False` —
the test will remind them why it's there. The test's docstring records the
**reason**, not just the fact: that is documentation for a non-obvious
decision.

There's also a lesson in fragile tests here. The first version of the assert
searched for the substring `'"stream": false'` and **failed**: `httpx`
serializes JSON compactly, with no space after the colon. The assert was
checking a detail of the library's implementation, not our code's behavior.
Fixed by parsing the body instead:

```python
def _sent_body(route: Any) -> dict[str, Any]:
    """Body of the last request sent to this route, as a dict."""
    return json.loads(route.calls.last.request.read().decode())
```

`tests/test_client.py`

Rule of thumb: **assert semantics, not formatting.**

### Failure-path tests

```python
async def test_401_explains_manage_scope(client: OmniRouteClient) -> None:
    ...
    with pytest.raises(OmniRouteError, match="manage"):
        await client.providers()
```

`tests/test_client.py`

This checks not the exception type, but the **message content**: it must
contain the word `manage`. For an ordinary service, an assert like that would
look odd — pinning tests to error text is generally considered bad practice.
But here, as established in chapter 4, the exception text goes **straight**
to the model and is part of the interface. So it deserves to be pinned by a
test.

A similar test for network errors (`tests/test_client.py`) checks that the
message contains the URL: the agent should see the address, not a bare
`ConnectError`.

And context protection:

```python
async def test_html_error_is_truncated(client: OmniRouteClient) -> None:
    respx.get(f"{BASE}/api/usage/quota").mock(
        return_value=httpx.Response(404, text="<!DOCTYPE html>" + "x" * 500_000)
    )
    with pytest.raises(OmniRouteError) as exc:
        await client.quota()
    assert len(str(exc.value)) < 500
```

`tests/test_client.py`

Half a megabyte of HTML in, under 500 characters in the exception. Exactly
the kind of case you cannot reproduce against a live gateway on demand — but
against a mock it's trivial.

### Resilience to response shape

```python
async def test_call_logs_accepts_bare_array(client: OmniRouteClient) -> None:
    respx.get(f"{BASE}/api/usage/call-logs").mock(
        return_value=httpx.Response(200, json=[{"id": "1", "status": 200}])
    )
    assert (await client.call_logs(1))[0]["id"] == "1"
```

`tests/test_client.py`

`/api/usage/call-logs` returns a **bare array**, not an object with a `logs`
field — unlike its neighboring endpoints. Such inconsistencies are typical of
live APIs, and the test pins down that knowledge.

## Formatter tests

13 tests, and they are the cheapest: pure functions, no mocks, no async.

### Checking both presence of signal and absence of noise

```python
assert "3.8.42" in text
assert "1d 1h" in text         # 91600 s ~= 1 day 1 hour
assert "cline" in text          # the degraded provider is named
assert "chipotle" not in text   # healthy — no need to mention it
assert "arrayBuffers" not in text
assert "80e3e260" not in text
```

`tests/test_formatting.py`

Notice the negative assertions — about half of them. This is a direct check
of the thesis from chapter 3: **context hygiene is testable.** We assert that
`memoryUsage` and UUIDs from `rateLimitStatus` never make it into the output.

Assertions like these guard against a typical form of decay: someone decides
"let's just return the whole health object, might come in handy" — and the
test blocks it.

The test inputs are fragments of **real gateway responses**, captured during
exploration. This partially compensates for the lack of DTO schemas mentioned
in chapter 1: the shape of the data is at least pinned down in the tests.

### Checking the arithmetic

```python
assert "Total requests: 2" in text
assert "failed: 1" in text
assert "30/5" in text     # sum of tokens
assert "200 ms" in text   # average duration
```

`tests/test_formatting.py`

These are exactly the computations we took away from the model (chapter 3).
By moving them into Python, we gained the ability to **test** them — which
would be impossible with an LLM.

### Edge cases

```python
def test_provider_stats_survives_zero_requests() -> None:
    """Division by zero is a classic way formatters crash."""
    text = formatting.format_provider_stats([{"provider": "new", "totalRequests": 0}])
    assert "new" in text
```

`tests/test_formatting.py`

Success rate is computed as `ok / total`. A new provider with zero requests
would crash the tool with a `ZeroDivisionError`. In the code this is handled
with a ternary (`formatting.py`); here, it's handled with a test.

A similar test covers an empty call log list (`tests/test_formatting.py`):
instead of crashing or returning an empty string, the agent gets a clear "no
calls in the last 6h".

## Pytest configuration

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`pyproject.toml`

`asyncio_mode = "auto"` removes the need for `@pytest.mark.asyncio` above
every async test. A small thing, but noticeable in a file with 9 async tests.

The client fixture (`tests/test_client.py`) is an async generator: it creates
the client, hands it to the test, closes it afterward. The same pattern as
`lifespan` in chapter 5, just scoped to a single test.

## Verification on the live system

Unit tests prove the code works as intended. They do **not** prove the intent
was correct. That's what the three live checks are for.

### Level 2: selftest

Covered in chapter 5. Result: `8/8 succeeded`, including a real call to the
model. Answers "is the gateway alive and were our assumptions about the
endpoints correct".

### Level 3: MCP handshake

`src/omniroute_agent/discovery.py` connects to the server **as a real agent
would** — through `stdio_client` from the MCP SDK. This is the only check
that covers the whole MCP layer: transport, primitive registration, schema
generation.

The result obtained:

```
Connected to: omniroute

Available tools (8):
  - omniroute_health — Check the status of the OmniRoute gateway.
  - omniroute_list_models — Show the models available through OmniRoute.
  ...
Resources (2): omniroute://health, omniroute://models
Prompts (2): diagnose_gateway, compare_models
```

Followed by a live tool call and a live inference call through
`omniroute_chat`, returning a meaningful answer with `isError=False`.

The file is also useful as teaching material: it shows the **client** side of
MCP, i.e. what Claude Code and Cursor do internally. Three steps —
`initialize`, `list_tools`, `call_tool` — and it becomes clear that discovery
really does happen at runtime.

### Checking the failure path

Separately verified: behavior without a key. `omniroute_list_providers`
returned `isError=True` with a hint about the `manage` scope, while
`omniroute_health` **kept working** — it sits on a PUBLIC route of the
gateway. A nice property: even with a misconfigured key, the agent retains
the ability to diagnose the problem.

### Final check: a real agent

```bash
claude mcp list
# omniroute: uv --directory … run omniroute-mcp - ✔ Connected
```

The only check that confirms the original goal: "any local agent can
connect."

## What the tests don't cover

An honest list of gaps.

**No tests for `server.py`.** Neither `lifespan`, nor transport selection, nor
`selftest` are covered by unit tests. This is covered by the live handshake
instead. Rationale: testing assembly in isolation is expensive (you'd need to
spin up a session), and the handshake checks the same thing more honestly.
The cost — regressions in `server.py` won't be caught by automated tests.

**No tests for docstrings.** The most valuable part of the tools (chapter 4)
is not checked by anything automated. Whether the model "understands" a
description can only be verified empirically, by observing agent behavior.
This is a limitation of the approach itself, not of the project.

**No check for consistency with the live API.** If OmniRoute changes a
response shape in a new version, the mocks will stay green while the server
breaks. That's caught by `--selftest`, which is exactly why it exists as a
separate mode.

## Summary

- Unit tests on mocks: fast, deterministic, reproduce failures on demand.
- We assert outgoing requests and error text — they are part of the
  interface for the model.
- Negative assertions pin down context hygiene.
- Assert semantics, not formatting (the `stream: false` story).
- Three levels of live checks: selftest → handshake → real agent.
- The gaps are known and deliberate, not accidental.

---

**Next:** [Chapter 7. Agents](07_agents.md) — the two server versions and the
autonomous agent loop.
