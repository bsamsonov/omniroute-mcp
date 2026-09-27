"""Client half of the project: an autonomous agent on top of the OmniRoute MCP server.

This is a package rather than a folder of scripts. The difference is
substantial, not cosmetic: a loose script only works from
the repository root and gets imported by a lucky accident (the
interpreter puts the script's directory on `sys.path`). A package is
installed together with the project, gets console commands, and can be
imported from anywhere.

Contents:

    core       — the agent's logic: the "model <-> tools" loop. Knows nothing about transport.
    stdio      — connects to a local server subprocess.
    http       — connects to a network server.
    discovery  — a bare MCP client with no LLM: shows what the server exposes.

Configuration is shared with the server, via `omniroute_mcp.config.Settings`
(and therefore via `.env`). The model is never hardcoded: `CLIENT_MODEL`.
"""
