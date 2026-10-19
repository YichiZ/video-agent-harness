"""E2E helpers: run `python -m server.cli` as a subprocess, as a user would.

`cli(*args, env={...})` runs in an isolated environment:
- `.env` is ignored (`HARNESS_ENV_FILE` points at a missing file), so a developer's
  real keys never change a test's outcome. Live tests pass `live=True` to keep it.
- Provider keys and Claude login variables are removed unless `live=True`.
- `HARNESS_FAKE_PROVIDERS=1` unless `live=True`.
- `HARNESS_WORKSPACE_DIR` is a per-test temp dir (`cli.workspace`).
Pass `env={"NAME": None}` to unset a variable, or a string to set it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from server.config import REPO_ROOT

ISOLATED = [
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "HARNESS_GCS_BUCKET",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "HARNESS_RECORD_FIXTURES",
]


@dataclass
class Cli:
    workspace: Path
    tmp: Path
    live: bool = False

    def env(self, overrides: dict[str, str | None] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env["HARNESS_WORKSPACE_DIR"] = str(self.workspace)
        env["PYTHONUNBUFFERED"] = "1"
        env["COLUMNS"] = "200"
        if not self.live:
            for name in ISOLATED:
                env.pop(name, None)
            env["HARNESS_ENV_FILE"] = str(self.tmp / "no.env")
            env["HARNESS_FAKE_PROVIDERS"] = "1"
            # An empty Claude config dir, so the developer's own login is not detected.
            env["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude-config")
        for name, value in (overrides or {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return env

    def __call__(
        self, *args: str, env: dict[str, str | None] | None = None, timeout: float = 300
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "server.cli", *args],
            cwd=REPO_ROOT,
            env=self.env(env),
            capture_output=True,
            text=True,
            timeout=timeout,
        )


@pytest.fixture
def cli(tmp_path: Path, request: pytest.FixtureRequest) -> Cli:
    live = request.node.get_closest_marker("live") is not None
    (tmp_path / "claude-config").mkdir()
    return Cli(workspace=tmp_path / "workspace", tmp=tmp_path, live=live)


WEB = REPO_ROOT / "web"


@pytest.fixture(scope="session")
def web_dist() -> Path:
    """`web/dist`, built once per session with npm (skips without npm)."""
    import shutil

    if shutil.which("npm") is None:
        pytest.skip("npm not found; the browser tests need the built UI")
    if not (WEB / "node_modules").is_dir():
        subprocess.run(["npm", "ci"], cwd=WEB, check=True, capture_output=True)
    subprocess.run(["npm", "run", "build"], cwd=WEB, check=True, capture_output=True)
    return WEB / "dist"


@pytest.fixture
def page(web_dist):
    """A Playwright page in Google Chrome, which (unlike Chromium) plays the H.264 proxy."""
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Error as e:
            pytest.skip(f"Google Chrome not available to Playwright: {e.message.splitlines()[0]}")
        page = browser.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.on("pageerror", lambda e: print("PAGEERROR", e))
        yield page
        browser.close()
