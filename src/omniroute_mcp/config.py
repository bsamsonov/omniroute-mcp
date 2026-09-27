"""Layer 1 - configuration.

The only place that reads environment variables. The rest of the
application gets a ready-made `Settings` object and knows nothing about
`os.environ`.

Lesson: an MCP server has no CLI dialog and no web form — it is launched
by the host agent as a subprocess. So the environment is the only
practical configuration channel, and it must be validated at startup,
not on the first request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .envfile import load_env_file

DEFAULT_BASE_URL = "http://localhost:20128"
DEFAULT_TIMEOUT = 120.0


class ConfigError(RuntimeError):
    """A setting is missing or invalid.

    A separate type lets entry points show a clear hint to the human
    instead of a traceback.
    """


@dataclass(frozen=True)
class Settings:
    """Immutable settings for connecting to OmniRoute."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT
    model: str = ""

    @classmethod
    def from_env(cls, env_start: Path | None = None) -> "Settings":
        """Builds settings from the environment, filling in sane defaults.

        Loads `.env` first (see `envfile.py`). The file is looked up once
        here rather than in every entry point: both the server and the
        client get the same configuration from a single source.
        """
        load_env_file(env_start)

        raw_timeout = os.environ.get("OMNIROUTE_TIMEOUT", "")
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
        except ValueError:
            # A malformed value must not crash the server — fall back to the default.
            timeout = DEFAULT_TIMEOUT

        return cls(
            # rstrip("/") avoids double slashes when joining paths.
            base_url=os.environ.get("OMNIROUTE_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            api_key=os.environ.get("OMNIROUTE_API_KEY", "").strip(),
            timeout=timeout,
            model=os.environ.get("CLIENT_MODEL", "").strip(),
        )

    def require_model(self) -> str:
        """Returns the configured model or explains what is missing.

        There is deliberately no default here. A hardcoded `auto/*` alias
        has caused trouble before: the gateway resolves it to a provider
        on the fly and sometimes returns `400 Invalid model`. Better to
        fail honestly at startup with a clear message than later with an
        error from the gateway.
        """
        if not self.model:
            raise ConfigError(
                "No model configured. Set CLIENT_MODEL in .env "
                "(e.g. CLIENT_MODEL=kr/claude-sonnet-4.5) or pass it "
                "explicitly: omniroute-agent --model <name>. To list available "
                "names: omniroute-discover."
            )
        return self.model

    @property
    def masked_key(self) -> str:
        """Key in a form suitable for logs and diagnostics.

        Secrets must never end up in stdout or in the model's context.
        """
        if not self.api_key:
            return "<not set>"
        if len(self.api_key) <= 12:
            return "***"
        return f"{self.api_key[:8]}…{self.api_key[-4:]}"
