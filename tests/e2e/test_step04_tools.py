"""Step 4 e2e: `server.cli tool <name> '<json>'` calls a video tool through the MCP server.

The `short` cases need Video-MME (skip in CI); the `cuts.mp4` cases run everywhere.
Offline mode replays `tests/fixtures/gemini/<media>/inspect*.json`.
"""

from __future__ import annotations

import json
import re

import pytest

from server.config import release_path

COLOR_QUESTION = "What color is the cell membrane of the virus-invaded cell?"


def tool(cli, name: str, args: dict, *extra: str, **kw):
    return cli("tool", name, json.dumps(args), *extra, **kw)


def check_short_tools(cli, short) -> None:
    hit = tool(cli, "search", {"query": "spikes"}, "--video", str(short))
    assert hit.returncode == 0, hit.stdout + hit.stderr
    first = json.loads(hit.stdout)["hits"][0]
    # The plan said 00:01-00:10; the real transcript starts this sentence at 0.2 s.
    assert 0 <= first["start"] <= 10, first
    assert "spikes" in first["text"].lower()

    transcript = json.loads(tool(cli, "get_transcript", {"start": 0, "end": 10}).stdout)
    assert transcript["segments"]
    assert all(s["start"] < 10 and s["end"] > 0 for s in transcript["segments"])

    frames = tool(cli, "get_frames", {"timestamps": [5, 40]})
    assert frames.returncode == 0, frames.stderr
    assert len(re.findall(r"\[image image/jpeg \d+ bytes\]", frames.stdout)) == 2

    inspect = tool(cli, "inspect", {"question": COLOR_QUESTION})
    assert inspect.returncode == 0, inspect.stdout + inspect.stderr
    answer = json.loads(inspect.stdout)
    assert "red" in answer["answer"].lower(), answer
    assert answer["observations"] and re.fullmatch(
        r"\d{2}:\d{2}", answer["observations"][0]["time"]
    )
    assert answer["cached"] is False

    again = json.loads(tool(cli, "inspect", {"question": COLOR_QUESTION}).stdout)
    assert again["cached"] is True and again["answer"] == answer["answer"]


def test_short_tools(cli, videomme):
    check_short_tools(cli, videomme("short"))


@pytest.mark.live
def test_short_tools_live(cli, videomme):
    check_short_tools(cli, videomme("short"))


def test_cuts_tools_and_limits(cli, test_media, tmp_path):
    cuts = str(test_media / "cuts.mp4")
    overview = tool(cli, "get_overview", {}, "--video", cuts)
    assert overview.returncode == 0, overview.stdout + overview.stderr
    assert json.loads(overview.stdout)["duration"] == "00:20"
    assert json.loads(tool(cli, "search", {"query": "anything"}).stdout)["hits"] == []  # no audio

    nine = tool(cli, "get_frames", {"timestamps": list(range(9))})
    assert nine.returncode == 2 and "at most 8 timestamps" in nine.stderr

    first = tool(cli, "inspect", {"question": "Is there a cut?", "start": 0, "end": 4})
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["cached"] is False

    limited = cli.tmp / "release.json"
    data = json.loads(release_path().read_text())
    limited.write_text(json.dumps({**data, "inspect_limit": 0}))
    env = {"HARNESS_RELEASE_FILE": str(limited)}
    cached = tool(cli, "inspect", {"question": "Is there a cut?", "start": 0, "end": 4}, env=env)
    assert json.loads(cached.stdout)["cached"] is True  # cache hits never count
    over = tool(cli, "inspect", {"question": "Something new?"}, env=env)
    assert over.returncode == 2 and "inspect limit reached" in over.stderr


def test_no_workspace_and_bad_args(cli):
    none = tool(cli, "get_overview", {})
    assert none.returncode == 2 and "no ingested video" in none.stderr
    bad = cli("tool", "search", "{not json")
    assert bad.returncode == 2 and "not valid JSON" in bad.stderr
