"""Step 7 e2e: `server.cli ingest <video> --only captions` writes `captions.json`, and
`server.cli tool list_shots` returns the shots with their captions.

Offline mode replays `tests/fixtures/gemini/<media>/captions*.json` (and the
`medium` transcript from `tests/fixtures/scribe/`). The `medium` cases need
Video-MME (skip in CI); the generated 25-minute `windowed.mp4` runs everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.workspace import video_id


def captions_for(cli, video: Path) -> dict:
    return json.loads((cli.workspace / video_id(video) / "captions.json").read_text())


def check_medium_captions(cli, medium: Path) -> None:
    result = cli("ingest", str(medium), "--only", "captions")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "captions: done" in result.stdout
    shots = captions_for(cli, medium)["shots"]
    assert shots and all(s["caption"].strip() for s in shots)
    on_screen = " | ".join(t for s in shots for t in s["on_screen_text"]).lower()
    assert "american express" in on_screen, on_screen  # shown but never spoken

    listed = cli("tool", "list_shots", "{}")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    data = json.loads(listed.stdout)
    assert [s["index"] for s in data["shots"]] == [s["index"] for s in shots]
    assert data["shots"][0]["caption"] == shots[0]["caption"]

    ranged = json.loads(cli("tool", "list_shots", '{"start": 60, "end": 90}').stdout)
    assert ranged["shots"]
    assert all(s["start_s"] < 90 and s["end_s"] > 60 for s in ranged["shots"])


def test_medium_captions(cli, videomme):
    check_medium_captions(cli, videomme("medium"))


@pytest.mark.live
def test_medium_captions_live(cli, videomme):
    check_medium_captions(cli, videomme("medium"))


def test_long_video_is_captioned_in_windows(cli, test_media):
    video = test_media / "windowed.mp4"
    result = cli("ingest", str(video), "--only", "captions")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "captions: done (50 shots in 2 calls)" in result.stdout

    data = captions_for(cli, video)
    assert data["windows"] == [{"start_s": 0, "end_s": 750}, {"start_s": 750, "end_s": 1500}]
    shots = data["shots"]
    assert [s["index"] for s in shots] == list(range(50))
    assert all(s["caption"] for s in shots)
    assert shots[25]["time"] == "12:30" and shots[25]["on_screen_text"] == ["PART 2"]

    again = cli("ingest", str(video), "--only", "captions")
    assert "captions: skipped" in again.stdout


def test_list_shots_before_captions(cli, test_media):
    cuts = test_media / "cuts.mp4"
    assert cli("ingest", str(cuts), "--only", "shots").returncode == 0
    result = cli("tool", "list_shots", '{"start": 5, "end": 9}')
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert [s["index"] for s in data["shots"]] == [1, 2]
    assert "not ready" in data["note"]
