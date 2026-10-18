"""Step 6 e2e: `server.cli ingest <video> --only shots` writes `shots.json`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.workspace import video_id


def shots_for(cli, video: Path) -> dict:
    return json.loads((cli.workspace / video_id(video) / "shots.json").read_text())


def assert_covers(shots: list[dict], duration: float) -> None:
    assert shots[0]["start_s"] == 0
    assert shots[-1]["end_s"] == pytest.approx(duration)
    for a, b in zip(shots, shots[1:], strict=False):
        assert a["end_s"] == b["start_s"], "gap or overlap"


def test_cuts_found_within_one_frame(cli, test_media):
    cuts = test_media / "cuts.mp4"
    result = cli("ingest", str(cuts), "--only", "shots")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe: done" in result.stdout
    assert "shots: done (5 shots from 4 cuts)" in result.stdout

    data = shots_for(cli, cuts)
    shots = data["shots"]
    assert data["duration_s"] == pytest.approx(20.0, abs=0.05)
    assert_covers(shots, data["duration_s"])
    frame = 1 / data["fps"]
    starts = [s["start_s"] for s in shots[1:]]
    assert starts == pytest.approx([4, 8, 12, 16], abs=frame + 1e-6)

    again = cli("ingest", str(cuts), "--only", "shots")
    assert "probe: skipped" in again.stdout
    assert "shots: skipped" in again.stdout


def test_medium_shots(cli, videomme):
    medium = videomme("medium")
    result = cli("ingest", str(medium), "--only", "shots")
    assert result.returncode == 0, result.stdout + result.stderr
    data = shots_for(cli, medium)
    shots = data["shots"]
    assert data["duration_s"] == pytest.approx(367, abs=1)
    assert_covers(shots, data["duration_s"])
    lengths = [s["end_s"] - s["start_s"] for s in shots]
    assert max(lengths) <= 30
    assert min(lengths) >= 1
