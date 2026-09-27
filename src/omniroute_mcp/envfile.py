"""Layer 0 - reading the `.env` file.

Why a homegrown loader instead of `python-dotenv`: we need exactly two
things — find the file and parse `KEY=VALUE`. Fifty lines with no
dependency are more honest than a package for a single function, and
in class they can be read in full.

**Precedence rule: the real environment beats `.env`.**
This is the convention across all dotenv loaders, and for MCP it is
essential: the host agent (Claude Desktop, Cursor) passes variables to
the server through its own `mcpServers.env` config. If `.env` overrode
them, the host's config would be silently ignored — and that would be
very hard to debug.

Where the file is looked up, in order:

    1. the path from `OMNIROUTE_ENV_FILE` — an explicit override, always wins;
    2. upward through the tree from the current working directory;
    3. upward through the tree from the package itself — helps a dev
       run where the server is spawned by the agent from a different
       working directory.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE_VAR = "OMNIROUTE_ENV_FILE"
ENV_FILE_NAME = ".env"


def parse_env_file(text: str) -> dict[str, str]:
    """Parses the contents of `.env` into a dict.

    Supports the necessary minimum: comments, blank lines, an `export`
    prefix, and quotes around a value. No variable expansion, no
    multiline values — needing those would be a sign to switch to
    `python-dotenv`.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]

        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        # Quotes are part of the file's syntax, not of the value.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def find_env_file(start: Path | None = None) -> Path | None:
    """Finds `.env` following the rules from the module docstring."""
    explicit = os.environ.get(ENV_FILE_VAR, "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None

    roots = [start or Path.cwd(), Path(__file__).resolve().parent]
    for root in roots:
        for directory in [root, *root.resolve().parents]:
            candidate = directory / ENV_FILE_NAME
            if candidate.is_file():
                return candidate
    return None


def load_env_file(start: Path | None = None) -> Path | None:
    """Merges values from `.env` into `os.environ`.

    Leaves already-set variables untouched (see the precedence rule
    above). Returns the path to the file that was read, or None if
    there is no file — a missing `.env` is not an error: in production
    the variables come from the environment.
    """
    path = find_env_file(start)
    if path is None:
        return None

    for key, value in parse_env_file(path.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, value)
    return path
