"""Step 12 e2e: the Skills load in the CLI chat, and prompt v2 scores no lower than v1.

Offline mode replays `tests/fixtures/agent/t6V9i8fFADI-chat.json` for the chat and
`tests/fixtures/agent/smoke/*.json` for the comparison. Live mode runs the real
model; `make test-live ARGS=--record` re-records both. Needs Video-MME, so it
skips in CI.
"""

from __future__ import annotations

import subprocess
import sys

import pexpect
import pytest

from server.config import REPO_ROOT
from tests.e2e.test_step05_chat import ask, spawn, tool_lines

SUMMARIZE = "Summarize this video."
FIND = "Find every moment where Warren Buffett is mentioned or shown."


def check_skills(cli, medium) -> None:
    child = spawn(cli, medium)
    summary = ask(child, SUMMARIZE)
    assert "Skill" in tool_lines(summary) and "summarize-video" in summary, summary
    assert "get_overview" in tool_lines(summary), summary
    moments = ask(child, FIND)
    assert "Skill" in tool_lines(moments) and "find-moments" in moments, moments
    assert "Buffett" in moments, moments
    child.sendline("/quit")
    child.expect(pexpect.EOF)


def check_smoke(cli) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "evals.smoke"],
        cwd=REPO_ROOT,
        env=cli.env(),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok: v2" in result.stdout, result.stdout


def test_skills(cli, videomme):
    check_skills(cli, videomme("medium"))


@pytest.mark.live
def test_skills_live(cli, videomme):
    check_skills(cli, videomme("medium"))


def test_smoke_comparison(cli, videomme):
    videomme("short"), videomme("medium")
    check_smoke(cli)


@pytest.mark.live
def test_smoke_comparison_live(cli, videomme):
    videomme("short"), videomme("medium")
    check_smoke(cli)
