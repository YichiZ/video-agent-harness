"""Step 9 e2e: `ingest <video>` runs every step in parallel and records `status.json`.

- A full ingest shows every step as done.
- `HARNESS_FAKE_FAIL=captions` makes captions fail: the command exits 3, the
  transcript and overview are still done, the index is blocked, and
  `--retry captions` completes the ingest.
- `chat` reaches its first prompt while captions are still running
  (`HARNESS_FAKE_SLOW=captions` offline), and quitting waits for the rest.

`cuts.mp4` runs everywhere; `medium` and the chat (on `short`, whose agent stream
is recorded) need Video-MME and skip in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pexpect
import pytest

from server.ingest import steps
from server.workspace import video_id
from tests.e2e.test_step05_chat import spawn

ALL = set(steps())


def status(cli, video: Path) -> dict:
    return json.loads((cli.workspace / video_id(video) / "status.json").read_text())


def states(cli, video: Path) -> dict[str, str]:
    return {name: s["state"] for name, s in status(cli, video)["steps"].items()}


def check_full_ingest(cli, video: Path) -> None:
    result = cli("ingest", str(video))
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ALL:
        assert f"{name}: done" in result.stdout, result.stdout
    assert states(cli, video) == dict.fromkeys(ALL, "done")
    assert status(cli, video)["chat_ready"] is True


def check_failure_and_retry(cli, video: Path) -> None:
    failed = cli("ingest", str(video), env={"HARNESS_FAKE_FAIL": "captions"})
    assert failed.returncode == 3, failed.stdout + failed.stderr
    assert "captions: failed (RuntimeError: injected failure" in failed.stdout
    assert "--retry" in failed.stderr
    got = states(cli, video)
    assert got["transcribe"] == got["overview"] == got["shots"] == "done", got
    assert (got["captions"], got["index"]) == ("failed", "blocked"), got
    assert status(cli, video)["chat_ready"] is True

    retry = cli("ingest", str(video), "--retry", "captions")
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert "captions: done" in retry.stdout and "index: done" in retry.stdout
    assert "transcribe: skipped" in retry.stdout
    assert states(cli, video) == dict.fromkeys(ALL, "done")


def check_chat_starts_early(cli, video: Path) -> None:
    child = spawn(cli, video)  # returns at the first prompt
    at_prompt = status(cli, video)
    assert at_prompt["chat_ready"] is True
    if not cli.live:  # live captions may already be cached or fast
        assert at_prompt["steps"]["captions"]["state"] != "done", at_prompt
    child.sendline("/quit")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 0, child.before
    assert states(cli, video) == dict.fromkeys(ALL, "done")


def test_cuts_orchestration(cli, test_media):
    cuts = test_media / "cuts.mp4"
    check_failure_and_retry(cli, cuts)
    rerun = cli("ingest", str(cuts))
    assert rerun.returncode == 0 and all(f"{n}: skipped" in rerun.stdout for n in ALL)


def test_medium_full_ingest(cli, videomme):
    check_full_ingest(cli, videomme("medium"))


def test_medium_failure_and_retry(cli, videomme):
    check_failure_and_retry(cli, videomme("medium"))


def test_chat_starts_before_captions(cli, videomme, monkeypatch):
    monkeypatch.setenv("HARNESS_FAKE_SLOW", "captions")  # cli.env() copies os.environ
    check_chat_starts_early(cli, videomme("short"))


@pytest.mark.live
def test_medium_full_ingest_live(cli, videomme):
    check_full_ingest(cli, videomme("medium"))


@pytest.mark.live
def test_chat_starts_before_captions_live(cli, videomme):
    check_chat_starts_early(cli, videomme("short"))


def test_chat_reports_a_background_failure(cli, videomme, monkeypatch):
    monkeypatch.setenv("HARNESS_FAKE_FAIL", "captions")
    short = videomme("short")
    child = spawn(cli, short)
    child.sendline("/quit")
    child.expect(pexpect.EOF)
    child.close()
    assert child.exitstatus == 3
    assert "captions failed" in child.before and "--retry" in child.before
