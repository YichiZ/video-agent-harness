"""Claude auth mode.

Decision (Yichi, 2026-10-18): use `ANTHROPIC_API_KEY` when it is set, otherwise
fall back to the local Claude Code login, which bills the Claude subscription.
`max_budget_usd` only applies in API-key mode.

Detecting the subscription login is a best-effort check for the credentials the
Claude Code CLI stores; `doctor --live` confirms it with a real call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from server.config import Settings


class AuthMode(StrEnum):
    API_KEY = "API key"
    SUBSCRIPTION = "Claude subscription (fallback)"
    NONE = "none found"


@dataclass(frozen=True)
class AuthStatus:
    mode: AuthMode
    detail: str


def _claude_config_dir() -> tuple[Path, bool]:
    """The Claude Code config dir, and whether `CLAUDE_CONFIG_DIR` overrides it."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser(), True
    return Path("~/.claude").expanduser(), False


def _macos_keychain_has_login() -> bool:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials"],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def detect_auth(settings: Settings) -> AuthStatus:
    if settings.anthropic_api_key:
        source = "environment" if os.environ.get("ANTHROPIC_API_KEY") else str(settings.env_file)
        return AuthStatus(AuthMode.API_KEY, f"ANTHROPIC_API_KEY from {source}")
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return AuthStatus(AuthMode.SUBSCRIPTION, "CLAUDE_CODE_OAUTH_TOKEN is set")
    config_dir, overridden = _claude_config_dir()
    credentials = config_dir / ".credentials.json"
    if credentials.is_file():
        return AuthStatus(AuthMode.SUBSCRIPTION, f"Claude Code login in {credentials}")
    # The macOS keychain entry is only meaningful for the default config dir.
    if sys.platform == "darwin" and not overridden and _macos_keychain_has_login():
        return AuthStatus(AuthMode.SUBSCRIPTION, "Claude Code login in the macOS keychain")
    return AuthStatus(
        AuthMode.NONE,
        "set ANTHROPIC_API_KEY in .env, or run `claude` once and log in",
    )


def claude_env(settings: Settings) -> dict[str, str]:
    """Extra env for the Agent SDK's Claude Code subprocess (`ClaudeAgentOptions.env`).

    In API-key mode the key is passed explicitly, so a key that lives only in
    `.env` still reaches the SDK. In subscription mode nothing is added, and the
    SDK uses the local login.
    """
    if settings.anthropic_api_key:
        return {"ANTHROPIC_API_KEY": settings.anthropic_api_key}
    return {}
