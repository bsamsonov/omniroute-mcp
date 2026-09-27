# Chapter 3. Formatting — a layer with no backend equivalent

This is the most important chapter for someone with backend experience,
because the layer it describes simply doesn't exist in a familiar
architecture. Its absence is the main reason homegrown MCP servers work
poorly.

## Why you can't just hand back the JSON

In a REST service you return JSON and don't think about its size: the
client ignores extra fields, and the cost of transferring it is
negligible.

With MCP it's different, because a tool's response goes **into the model's
context window**. Three things change:

1. **Size costs money.** Tokens are billed on every call.
2. **Size costs attention.** The context is finite, and clutter pushes out
   what's useful.
3. **Noise hurts quality.** The model finds the relevant bit less reliably
   amid `arrayBuffers` and UUIDs.

Which leads to what I consider the central thesis of the whole project:

> **The quality of an MCP server is defined by what it does NOT put into
> the context.**

## In numbers: 22 fields vs six lines

`GET /api/monitoring/health` returns ~1.2 KB and 22 top-level fields.
Here's what's in there besides the useful part:

```json
{
  "memoryUsage": {"rss": 360386560, "heapTotal": 301993984,
                  "heapUsed": 294508808, "arrayBuffers": 14207100},
  "rateLimitStatus": {
    "opencode:80e3e260-a95d-4423-89bc-aac7eeea4688": {"queued": 0, "running": 0},
    "openai-compatible-chat-c2ed2087-8d90-4dac-be37-2e6dbebc9fdd:dcc0a2b4-…": {…}
  },
  "learnedLimits": {...}, "lockouts": {...}, "dedup": {...}, "cryptography": {...}
}
```

Ask yourself, as an architect: **what decision would the agent make based
on `arrayBuffers: 14207100`?** None. And `rateLimitStatus` with UUID keys
is hundreds of tokens from which the model extracts nothing, because UUIDs
mean nothing to it.

The result of `format_health()` (`src/omniroute_mcp/formatting.py:49`) is
six lines and ~300 bytes:

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

Four times more compact — and **more informative**, because there's no need
to dig the signal out of the noise.

## Trick: show only deviations

The most valuable part of the formatter is the last few lines:

```python
unhealthy = [
    f"  ⚠ {name}: {info.get('state')} (failures: {info.get('failures', 0)})"
    for name, info in (data.get("providerHealth") or {}).items()
    if isinstance(info, dict) and info.get("state") not in ("CLOSED", None)
]
```

`formatting.py:73`

There can be many providers, and **healthy ones aren't interesting to the
agent** — they're already counted in the circuit-breaker line. We name only
the problem ones.

This scales: with five providers or fifty, the output stays short while
everything is fine and grows only by the problem entries. The analogy from
your own experience — a WARN-level log instead of a full DEBUG dump of the
whole state.

Notice `(data.get("providerHealth") or {})`. Not just `.get(..., {})`:
OmniRoute can return `null` in this field, and then the `{}` default
doesn't kick in — `.get()` returns `None` as is. These small details are
what make formatters robust against a live API.

## Trick: sort by importance

Dumping models as a flat list is a bad answer once there are enough of
them. `format_models()` (`formatting.py:85`) splits them into two groups:

```python
auto = [m for m in models if str(m.get("id", "")).startswith("auto/")]
regular = [m for m in models if not str(m.get("id", "")).startswith("auto/")]
```

`formatting.py:107`

`auto/*` entries aren't models — they're **routing strategies**:
`auto/cheap`, `auto/best-coding`. OmniRoute itself picks the provider
behind them. For an agent this is the most convenient and safest choice,
so it comes first instead of getting lost in a long flat list.

Order in the output is a priority signal. A model reads sequentially, and
whatever is higher up influences the decision more. A cheap way to steer
the agent toward the right choice without writing a single word of
instructions.

This is also where fields get trimmed. Each model has around ten fields
(`permission`, `created`, `root`, `parent`, `owned_by`…). We keep only `id`
and the context window:

```python
def describe(model: dict[str, Any]) -> str:
    line = f"  {model.get('id')}"
    context = model.get("context_length")
    if context:
        line += f" (context: {int(context) // 1024}k)"
    return line
```

`formatting.py:110`

`1048576` becomes `1024k`. Easier to read, fewer tokens.

## Trick: compute on the server, not with the model

Now the second most important thesis of the chapter.
`format_usage_summary()` (`formatting.py:220`) receives an array of logs
and does all the arithmetic itself:

```python
total = len(logs)
failed = sum(1 for log in logs if isinstance(log.get("status"), int) and log["status"] >= 400)
tokens_in = sum((log.get("tokens") or {}).get("in") or 0 for log in logs)
```

`formatting.py:230`

The alternative — hand the model raw logs and ask it to add them up — is
bad for three reasons at once: it's **expensive** (hundreds of entries in
the context), **unreliable** (LLMs make arithmetic mistakes), and
**untestable** (you can't write a unit test for its calculations).

Rule: **compute what's computable, in code.** The model is good at
interpretation and phrasing, not at summing numbers. Give it the result
and let it draw conclusions.

The same layer builds top lists by model and provider — and truncates
them:

```python
sorted(by_model.items(), key=lambda kv: kv[1], reverse=True)[:10]
```

`formatting.py:253`

`[:10]` is a volume cap. Without it, a full day of active use would push
dozens of entries into the output. A top 10 answers "what am I mostly
using"; the tail doesn't matter.

Notice `(log.get("tokens") or {}).get("in") or 0`. Double protection: the
`tokens` field can be missing, and `in` inside it can be `null` — which
does happen in real gateway logs for failed calls. One `None` inside
`sum()` and the whole tool crashes with a `TypeError`.

## Trick: show the decision, not just the result

`format_call_logs()` (`formatting.py:192`) is my favorite formatter,
because it doesn't just relay data — it **explains the system's
behavior**:

```python
requested = log.get("comboName") or log.get("requestedModel") or "?"
actual = log.get("model") or "?"
route = f"{requested} → {actual}" if requested != actual else actual
```

`formatting.py:206`

Output:

```
✓ 08:30:09 [200] auto/cheap → big-pickle (opencode), 9914 ms, tokens 264/69
```

The pair "what was requested → where it went" is exactly what routing —
OmniRoute's main function — does. The line `auto/cheap → big-pickle`
immediately answers "what's actually going on", a question that would
otherwise require the agent to do its own digging.

The arrow is only drawn when the model actually got swapped. No routing
happened — no arrow, to avoid a false impression.

The `✓` / `✗` symbols are also a trick: one character instead of the words
"succeeded"/"failed", and the model reads them perfectly well.

## Trick: explain an empty result

```python
if not content:
    return (
        "The model returned an empty response. For reasoning models, "
        "hidden reasoning may have consumed the whole budget — increase max_tokens."
    )
```

`formatting.py:275`

An empty string is the worst possible answer for an agent: it can't tell
whether something broke or whether that's expected. Here we state the
**likely cause and the fix** — the same lesson about reasoning tokens from
chapter 2.

And on success we append a footer:

```python
footer = (
    f"\n\n---\n[model: {data.get('model', '?')}; "
    f"tokens: {usage.get('prompt_tokens', '?')} in / "
    f"{usage.get('completion_tokens', '?')} out]"
)
```

`formatting.py:283`

Why: the agent asked for `auto/cheap`, but `big-pickle` actually handled
it — it should see where the request really went and what it cost. **The
agent should see the price of its own decision**, or it will never learn
to pick a model sensibly.

## What makes these functions pleasant to work with

All of them are pure: a `dict` in, a `str` out. No network, no state, no
async. Two consequences follow.

**They're easy to test.** 13 of the project's 22 tests are about
formatters, and all of them run in milliseconds with no OmniRoute instance
running (chapter 6).

**They're easy to change.** Don't like the output — fix one function,
touch neither the client nor MCP. Formatting is what most often needs
tuning after watching how the agent actually behaves in practice, and it's
good to have an isolated place for that.

## Summary

Five tricks that apply to any MCP server:

1. **Drop anything the agent can't act on** (`memoryUsage`, UUIDs).
2. **Show deviations, not full state** (only problem providers).
3. **Sort by importance and cut the tail** (`auto/*` first, top 10).
4. **Compute in code, not with the model** (aggregates, success rate).
5. **Explain empty or odd results** (the `max_tokens` hint).

---

**Next:** [Chapter 4. MCP primitives](04_mcp_primitives.md) — tools,
resources, prompts, and why the docstring matters more than the code here.
