"""Tests for the formatting layer.

We check two properties that matter specifically for MCP:
1. the output contains what the agent needs to make a decision;
2. the output does NOT contain noise (this is context hygiene).
"""

from __future__ import annotations

from omniroute_mcp import formatting


def test_health_keeps_signal_drops_noise() -> None:
    text = formatting.format_health(
        {
            "status": "healthy",
            "version": "3.8.42",
            "uptime": 91600,
            "activeConnections": 6,
            "circuitBreakers": {"open": 0, "degraded": 1, "closed": 1},
            "providerSummary": {"activeCount": 4, "configuredCount": 5, "catalogCount": 242},
            "providerHealth": {
                "cline": {"state": "DEGRADED", "failures": 6},
                "chipotle": {"state": "CLOSED", "failures": 1},
            },
            # Noise that must be dropped:
            "memoryUsage": {"arrayBuffers": 14207100, "rss": 360386560},
            "rateLimitStatus": {"opencode:80e3e260-a95d-4423": {"queued": 0}},
        }
    )
    assert "3.8.42" in text
    assert "1d 1h" in text  # 91600 s ~= 1 day 1 hour
    assert "cline" in text  # the degraded provider is named
    assert "chipotle" not in text  # healthy — no need to mention it
    assert "arrayBuffers" not in text
    assert "80e3e260" not in text


def test_models_puts_auto_first() -> None:
    text = formatting.format_models(
        [
            {"id": "pepper/pepper-1", "context_length": 128000},
            {"id": "auto/cheap", "context_length": 1048576},
        ]
    )
    assert text.index("auto/cheap") < text.index("pepper/pepper-1")
    assert "1024k" in text


def test_models_filter_matches_id() -> None:
    models = [{"id": "auto/cheap"}, {"id": "ddgw/gpt-5-mini"}]
    assert "gpt-5-mini" in formatting.format_models(models, "gpt")
    assert "auto/cheap" not in formatting.format_models(models, "gpt")
    assert "No models found" in formatting.format_models(models, "llama")


def test_call_logs_show_routing_decision() -> None:
    """The "what was requested -> where it went" pair is what explains routing."""
    text = formatting.format_call_logs(
        [
            {
                "timestamp": "2026-07-25T08:20:59.888Z",
                "status": 200,
                "comboName": "auto/cheap",
                "model": "big-pickle",
                "provider": "opencode",
                "duration": 5726,
                "tokens": {"in": 253, "out": 146},
            }
        ]
    )
    assert "auto/cheap → big-pickle" in text
    assert "08:20:59" in text
    assert "✓" in text


def test_call_logs_marks_failure() -> None:
    text = formatting.format_call_logs(
        [{"status": 502, "model": "x", "error": "server_error", "tokens": {}}]
    )
    assert "✗" in text
    assert "server_error" in text


def test_usage_summary_does_the_arithmetic() -> None:
    """Python does the arithmetic, not the LLM — cheaper and more reliable."""
    text = formatting.format_usage_summary(
        {
            "logs": [
                {"status": 200, "model": "a", "provider": "p1", "duration": 100,
                 "tokens": {"in": 10, "out": 5}},
                {"status": 502, "model": "b", "provider": "p2", "duration": 300,
                 "tokens": {"in": 20, "out": 0}},
            ]
        },
        hours=24,
    )
    assert "Total requests: 2" in text
    assert "failed: 1" in text
    assert "30/5" in text  # sum of tokens
    assert "200 ms" in text  # average duration


def test_usage_summary_handles_empty() -> None:
    assert "went through" in formatting.format_usage_summary({"logs": []}, hours=6)


def test_chat_completion_appends_routing_metadata() -> None:
    """The agent should see where the request went and how many tokens it cost."""
    text = formatting.format_chat_completion(
        {
            "model": "big-pickle",
            "choices": [{"message": {"content": "MCP_OK"}}],
            "usage": {"prompt_tokens": 2253, "completion_tokens": 146},
        }
    )
    assert text.startswith("MCP_OK")
    assert "big-pickle" in text
    assert "2253" in text


def test_chat_completion_explains_empty_reasoning_answer() -> None:
    """An empty reasoning-model answer must be explained, not silently returned as-is."""
    text = formatting.format_chat_completion({"model": "m", "choices": [{"message": {}}]})
    assert "max_tokens" in text


def test_quota_flags_bad_token() -> None:
    text = formatting.format_quota(
        [{"provider": "cline", "name": "B", "quotaUsed": 0, "quotaTotal": None,
          "percentRemaining": 100, "tokenStatus": "expiring"}]
    )
    assert "expiring" in text


def test_provider_stats_computes_success_rate() -> None:
    text = formatting.format_provider_stats(
        [{"provider": "Kiro AI", "totalRequests": 34, "successfulRequests": 20,
          "avgLatencyMs": 1547}]
    )
    assert "59%" in text  # 20/34


def test_provider_stats_survives_zero_requests() -> None:
    """Division by zero is a classic way formatters crash."""
    text = formatting.format_provider_stats([{"provider": "new", "totalRequests": 0}])
    assert "new" in text


def test_human_duration() -> None:
    assert formatting.human_duration(90) == "1m"
    assert formatting.human_duration(3661) == "1h 1m"
    assert formatting.human_duration(91600) == "1d 1h"
