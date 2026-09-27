"""Tests for the configuration layer: .env parsing, precedence, required model.

Important detail for all tests below: `Settings.from_env()` searches for
`.env` upward through the directory tree and, in a real run, would find
the project's actual file. That is why every test explicitly sets
`OMNIROUTE_ENV_FILE` — otherwise the result would depend on the machine
running the tests.
"""

from __future__ import annotations

import pytest

from omniroute_mcp.config import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, ConfigError, Settings
from omniroute_mcp.envfile import ENV_FILE_VAR, find_env_file, load_env_file, parse_env_file

OMNIROUTE_VARS = [
    "OMNIROUTE_BASE_URL",
    "OMNIROUTE_API_KEY",
    "OMNIROUTE_TIMEOUT",
    "CLIENT_MODEL",
    ENV_FILE_VAR,
]


@pytest.fixture
def clean_env(monkeypatch):
    """Removes project variables so the machine's environment does not affect the test."""
    for name in OMNIROUTE_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def write_env(tmp_path, text: str):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# File parsing
# ----------------------------------------------------------------------


def test_parse_handles_comments_blanks_export_and_quotes():
    text = "\n".join(
        [
            "# comment",
            "",
            "OMNIROUTE_API_KEY=sk-123",
            "export CLIENT_MODEL=kr/claude-sonnet-4.5",
            'QUOTED="quoted"',
            "SINGLE='single-quoted'",
            "garbage without an equals sign",
        ]
    )
    assert parse_env_file(text) == {
        "OMNIROUTE_API_KEY": "sk-123",
        "CLIENT_MODEL": "kr/claude-sonnet-4.5",
        "QUOTED": "quoted",
        "SINGLE": "single-quoted",
    }


def test_parse_keeps_equals_sign_inside_value():
    # The value may contain '=' — split only on the first occurrence.
    assert parse_env_file("URL=http://h/?a=1&b=2") == {"URL": "http://h/?a=1&b=2"}


# ----------------------------------------------------------------------
# File lookup and precedence
# ----------------------------------------------------------------------


def test_explicit_path_wins_over_search(clean_env, tmp_path):
    explicit = write_env(tmp_path, "CLIENT_MODEL=from-explicit-file")
    clean_env.setenv(ENV_FILE_VAR, str(explicit))
    assert find_env_file() == explicit


def test_file_is_found_upwards_from_cwd(clean_env, tmp_path):
    write_env(tmp_path, "CLIENT_MODEL=from-above")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_env_file(nested) == tmp_path / ".env"


def test_real_environment_beats_env_file(clean_env, tmp_path):
    """Key rule: the host agent's config beats the .env file."""
    clean_env.setenv(ENV_FILE_VAR, str(write_env(tmp_path, "CLIENT_MODEL=from-file")))
    clean_env.setenv("CLIENT_MODEL", "from-environment")

    load_env_file()

    assert Settings.from_env().model == "from-environment"


def test_missing_env_file_is_not_an_error(clean_env, tmp_path):
    clean_env.setenv(ENV_FILE_VAR, str(tmp_path / "no-such-file"))
    assert load_env_file() is None
    assert Settings.from_env().model == ""


# ----------------------------------------------------------------------
# Building Settings
# ----------------------------------------------------------------------


def test_settings_are_read_from_env_file(clean_env, tmp_path):
    path = write_env(
        tmp_path,
        "OMNIROUTE_BASE_URL=http://gw:1234/\n"
        "OMNIROUTE_API_KEY=sk-secret-value\n"
        "OMNIROUTE_TIMEOUT=42\n"
        "CLIENT_MODEL=kr/claude-sonnet-4.5\n",
    )
    clean_env.setenv(ENV_FILE_VAR, str(path))

    settings = Settings.from_env()

    assert settings.base_url == "http://gw:1234"  # trailing slash stripped
    assert settings.api_key == "sk-secret-value"
    assert settings.timeout == 42.0
    assert settings.model == "kr/claude-sonnet-4.5"


def test_broken_timeout_falls_back_to_default(clean_env, tmp_path):
    clean_env.setenv(ENV_FILE_VAR, str(write_env(tmp_path, "OMNIROUTE_TIMEOUT=soon")))
    settings = Settings.from_env()
    assert settings.timeout == DEFAULT_TIMEOUT
    assert settings.base_url == DEFAULT_BASE_URL


def test_require_model_returns_configured_value():
    assert Settings(model="kr/claude-sonnet-4.5").require_model() == "kr/claude-sonnet-4.5"


def test_require_model_explains_what_to_do_when_unset():
    # There is deliberately no default: let it fail at startup with a clear message.
    with pytest.raises(ConfigError, match="CLIENT_MODEL"):
        Settings().require_model()


def test_masked_key_never_leaks_full_secret():
    settings = Settings(api_key="sk-0123456789abcdef")  # gitleaks:allow (fake fixture)
    assert settings.api_key not in settings.masked_key
    assert settings.masked_key == "sk-01234…cdef"
    assert Settings().masked_key == "<not set>"
