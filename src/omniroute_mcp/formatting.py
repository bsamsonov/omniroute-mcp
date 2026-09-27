"""Layer 3 - turning the gateway's JSON into compact text for the model.

The main lesson about MCP here: **the quality of a server is defined by
what it does NOT put into the context.**

Example: `/api/monitoring/health` returns ~1.2 KB with 22 top-level
fields, including `memoryUsage.arrayBuffers` and a `rateLimitStatus`
dict full of connection UUIDs. The agent needs 6 lines out of that.
Every extra token here is paid for on every single tool call and dilutes
the model's attention.

That is why formatting lives in its own layer: it is easy to read, easy
to test, and easy to change without touching the client or the tool
definitions.
"""

from __future__ import annotations

from typing import Any

# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def human_duration(seconds: float) -> str:
    """3661 -> '1h 1m'. Uptime in raw seconds is not comfortable to read."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _short_time(iso: str | None) -> str:
    """'2026-07-25T08:20:59.888Z' -> '08:20:59'."""
    if not iso or "T" not in iso:
        return "-"
    return iso.split("T", 1)[1].split(".")[0].rstrip("Z")


# ----------------------------------------------------------------------
# Formatters, one per tool
# ----------------------------------------------------------------------


def format_health(data: dict[str, Any]) -> str:
    """Compresses the health response down to what affects the agent's decisions."""
    system = data.get("system", {})
    breakers = data.get("circuitBreakers", {})
    summary = data.get("providerSummary", {})

    lines = [
        f"OmniRoute: {data.get('status', 'unknown')}",
        f"Version: {data.get('version') or system.get('version', '?')}",
        f"Uptime: {human_duration(data.get('uptime', 0))}",
        f"Active connections: {data.get('activeConnections', 0)}",
        (
            f"Providers: {summary.get('activeCount', '?')} active "
            f"out of {summary.get('configuredCount', '?')} configured "
            f"(catalog: {summary.get('catalogCount', '?')})"
        ),
        (
            f"Circuit breakers: {breakers.get('closed', 0)} closed, "
            f"{breakers.get('degraded', 0)} degraded, "
            f"{breakers.get('open', 0)} open"
        ),
    ]

    # Unhealthy providers are named individually — that is the actionable part.
    unhealthy = [
        f"  ⚠ {name}: {info.get('state')} (failures: {info.get('failures', 0)})"
        for name, info in (data.get("providerHealth") or {}).items()
        if isinstance(info, dict) and info.get("state") not in ("CLOSED", None)
    ]
    if unhealthy:
        lines.append("Problem providers:")
        lines.extend(unhealthy)

    return "\n".join(lines)


def format_models(models: list[dict[str, Any]], filter_text: str = "") -> str:
    """List of models from `/v1/models` (OpenAI format: the `id` field).

    The `auto/*` pseudo-models are moved to the front: they are not
    models but routing strategies (`auto/cheap`, `auto/best-coding`)
    where OmniRoute itself picks the provider. For the agent this is the
    most useful and safest choice — so it comes first instead of
    drowning in a list of 125 entries.

    Out of a dozen fields per model, only `id` and the context window
    are kept: `permission`, `created`, `root`, `parent` mean nothing to
    the agent.
    """
    if filter_text:
        needle = filter_text.lower()
        models = [
            m
            for m in models
            if needle in str(m.get("id", "")).lower() or needle in str(m.get("name", "")).lower()
        ]

    if not models:
        return f"No models found matching filter '{filter_text}'."

    auto = [m for m in models if str(m.get("id", "")).startswith("auto/")]
    regular = [m for m in models if not str(m.get("id", "")).startswith("auto/")]

    def describe(model: dict[str, Any]) -> str:
        line = f"  {model.get('id')}"
        context = model.get("context_length")
        if context:
            # 1048576 -> "1024k": shorter and easier to read.
            line += f" (context: {int(context) // 1024}k)"
        return line

    out: list[str] = [f"Available models: {len(models)}"]

    if auto:
        out.append(f"\nAuto-routing ({len(auto)}) — OmniRoute picks the provider itself:")
        out.extend(describe(m) for m in auto)

    if regular:
        out.append(f"\nSpecific models ({len(regular)}):")
        out.extend(describe(m) for m in regular)

    return "\n".join(out)


def format_providers(connections: list[dict[str, Any]]) -> str:
    """Provider connections: activity, auth type, last error."""
    if not connections:
        return "OmniRoute has no configured provider connections."

    lines = [f"Connections: {len(connections)}"]
    for c in connections:
        status = "active" if c.get("isActive") else "disabled"
        title = c.get("name") or c.get("provider", "?")
        lines.append(
            f"\n{c.get('provider')} — {title}"
            f"\n  status: {status}, auth: {c.get('authType', '?')}"
            f", priority: {c.get('priority', '?')}"
        )
        if c.get("tokenExpiresAt"):
            lines.append(f"  token expires: {c['tokenExpiresAt']}")
        # Last error is the single most valuable field for diagnostics.
        if c.get("lastError"):
            lines.append(f"  ⚠ last error: {c['lastError']}")
    return "\n".join(lines)


def format_quota(providers: list[dict[str, Any]]) -> str:
    """Quotas and token status — the agent uses these to decide who gets the request."""
    if not providers:
        return "No quota data available."

    lines = ["Provider quotas:"]
    for p in providers:
        total = p.get("quotaTotal")
        used = p.get("quotaUsed", 0)
        amount = f"{used}/{total}" if total else f"{used} (no limit set)"
        token_flag = "" if p.get("tokenStatus") == "valid" else f" ⚠ token: {p.get('tokenStatus')}"
        lines.append(
            f"  {p.get('provider')} ({p.get('name')}): "
            f"used {amount}, remaining {p.get('percentRemaining', '?')}%{token_flag}"
        )
        if p.get("resetAt"):
            lines.append(f"    reset: {p['resetAt']}")
    return "\n".join(lines)


def format_provider_stats(providers: list[dict[str, Any]]) -> str:
    """Success rate and latency. We compute success rate — raw counts alone say little."""
    if not providers:
        return "No provider stats yet."

    lines = ["Provider stats (all time):"]
    # Sorted by traffic volume: what is actually used shows up on top.
    for p in sorted(providers, key=lambda x: x.get("totalRequests", 0), reverse=True):
        total = p.get("totalRequests", 0)
        ok = p.get("successfulRequests", 0)
        rate = f"{ok / total * 100:.0f}%" if total else "-"
        lines.append(
            f"  {p.get('provider')}: {total} requests, {rate} successful, "
            f"average latency {p.get('avgLatencyMs', 0)} ms, "
            f"tokens in/out: {p.get('totalTokensIn', 0)}/{p.get('totalTokensOut', 0)}"
        )
    return "\n".join(lines)


def format_call_logs(logs: list[dict[str, Any]]) -> str:
    """Recent calls — a self-diagnostic tool for the agent.

    We show `comboName` (what was requested) next to `model` (where it
    actually ended up) — that pair is exactly what explains routing.
    """
    if not logs:
        return "No calls have gone through the gateway yet."

    lines = [f"Recent calls ({len(logs)}):"]
    for log in logs:
        status = log.get("status")
        mark = "✓" if isinstance(status, int) and status < 400 else "✗"
        tokens = log.get("tokens") or {}
        requested = log.get("comboName") or log.get("requestedModel") or "?"
        actual = log.get("model") or "?"
        # The arrow is only drawn when routing actually swapped the model.
        route = f"{requested} → {actual}" if requested != actual else actual
        lines.append(
            f"  {mark} {_short_time(log.get('timestamp'))} [{status}] {route}"
            f" ({log.get('provider', '?')}), {log.get('duration', 0)} ms,"
            f" tokens {tokens.get('in', 0)}/{tokens.get('out', 0)}"
        )
        if log.get("error"):
            lines.append(f"      error: {log['error']}")
    return "\n".join(lines)


def format_usage_summary(export: dict[str, Any], hours: int) -> str:
    """Aggregate for the period. Computed on the server side, not by the model.

    This is a deliberate choice: making the LLM add up hundreds of
    numbers is both expensive and unreliable. Python does the
    arithmetic, the model gets the result.
    """
    logs = export.get("logs") or []
    if not logs:
        return f"No calls went through the gateway in the last {hours} h."

    total = len(logs)
    failed = sum(1 for log in logs if isinstance(log.get("status"), int) and log["status"] >= 400)
    tokens_in = sum((log.get("tokens") or {}).get("in") or 0 for log in logs)
    tokens_out = sum((log.get("tokens") or {}).get("out") or 0 for log in logs)

    durations = [log.get("duration") or 0 for log in logs]
    avg_ms = sum(durations) / len(durations) if durations else 0

    by_model: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    for log in logs:
        by_model[log.get("model") or "?"] = by_model.get(log.get("model") or "?", 0) + 1
        by_provider[log.get("provider") or "?"] = by_provider.get(log.get("provider") or "?", 0) + 1

    lines = [
        f"Summary for the last {hours} h:",
        f"  Total requests: {total} (failed: {failed})",
        f"  Tokens in/out: {tokens_in}/{tokens_out}",
        f"  Average duration: {avg_ms:.0f} ms",
        "  Models:",
    ]
    lines.extend(
        f"    {name}: {count}"
        for name, count in sorted(by_model.items(), key=lambda kv: kv[1], reverse=True)[:10]
    )
    lines.append("  Providers:")
    lines.extend(
        f"    {name}: {count}"
        for name, count in sorted(by_provider.items(), key=lambda kv: kv[1], reverse=True)[:10]
    )
    return "\n".join(lines)


def format_chat_completion(data: dict[str, Any]) -> str:
    """The model's answer plus a short note on where the request went.

    Routing metadata (which model actually ran, how many tokens) is
    returned deliberately: the agent should see the cost of its decision.
    """
    choices = data.get("choices") or []
    if not choices:
        return "OmniRoute returned a response with no choices (choices is empty)."

    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        # Typical for reasoning models when max_tokens is set too low.
        return (
            "The model returned an empty response. For reasoning models, "
            "hidden reasoning may have consumed the whole budget — increase max_tokens."
        )

    usage = data.get("usage") or {}
    footer = (
        f"\n\n---\n[model: {data.get('model', '?')}; "
        f"tokens: {usage.get('prompt_tokens', '?')} in / "
        f"{usage.get('completion_tokens', '?')} out]"
    )
    return f"{content}{footer}"
