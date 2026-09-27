"""Layer 6 - MCP prompts.

The third and most underrated protocol primitive.

    tool     — called by the MODEL (on its own, by its own decision)
    resource — loaded by the CLIENT (as context)
    prompt   — run by the HUMAN (a template, usually as a slash command)

A prompt is a reusable recipe: instead of explaining the diagnostic
procedure to the agent every time, you package it into a template. In
Claude Code these prompts show up as commands like
`/mcp__omniroute__diagnose_gateway`.

A useful point for class: a prompt gives the agent a *sequence* of tool
calls. Tools are capabilities, a prompt is a method for using them.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Registers ready-made scenarios for working with OmniRoute."""

    @mcp.prompt()
    def diagnose_gateway() -> str:
        """Full diagnostics for the OmniRoute gateway: what is broken and why."""
        return (
            "Run diagnostics on the OmniRoute gateway. Proceed in order:\n\n"
            "1. Call omniroute_health — assess overall status and circuit breakers.\n"
            "2. Call omniroute_list_providers — look for inactive connections, "
            "expiring tokens, and recent errors.\n"
            "3. Call omniroute_quota — check whether any quotas are exhausted.\n"
            "4. Call omniroute_recent_calls with limit=20 — find failed requests.\n"
            "5. Call omniroute_provider_stats — compare providers' success rates.\n\n"
            "Then give a short conclusion: (a) is the gateway healthy, (b) what "
            "specific problems were found, (c) what to do about them. Do not just "
            "restate the raw tool output — synthesize it."
        )

    @mcp.prompt()
    def compare_models(task: str) -> str:
        """Run one task through several models and compare the answers.

        Args:
            task: the task or question to ask the models.
        """
        return (
            f"Compare how different models handle this task:\n\n{task}\n\n"
            "Steps:\n"
            "1. Call omniroute_chat with model='auto/cheap'.\n"
            "2. Call omniroute_chat with model='auto/best-reasoning'.\n"
            "3. Compare the answers on substance, accuracy, and cost "
            "(the model and token counts are listed in each answer's footer).\n"
            "4. Recommend which model is better suited for this kind of task and why."
        )
