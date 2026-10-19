"""Step 1 e2e: `server.cli ingest <video> --only probe` builds and validates the workspace.

The Video-MME cases run on a machine with `VIDEO_MME_DIR` (skip in CI); the
generated-media cases run everywhere.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from server.workspace import video_id


def meta_for(cli, video: Path) -> dict:
    return json.loads((cli.workspace / video_id(video) / "meta.json").read_text())


# Video-MME (plan cases)


def test_short_probe(cli, videomme):
    short = videomme("short")
    result = cli("ingest", str(short), "--only", "probe")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe: done" in result.stdout
    meta = meta_for(cli, short)
    assert meta["duration_s"] == pytest.approx(83.9, abs=0.1)
    assert meta["source_name"] == "TbaCxIJ_VP4.mp4"
    assert meta["audio"] is not None

    again = cli("ingest", str(short), "--only", "probe")
    assert again.returncode == 0
    assert "probe: skipped" in again.stdout


def test_long_is_accepted_at_cap_boundary(cli, videomme):
    long = videomme("long")
    result = cli("ingest", str(long), "--only", "probe")
    assert result.returncode == 0, result.stdout + result.stderr
    assert 3500 < meta_for(cli, long)["duration_s"] <= 3600


# Generated media (also run in CI)


def test_generated_video_probe_and_rerun(cli, test_media):
    cuts = test_media / "cuts.mp4"
    result = cli("ingest", str(cuts), "--only", "probe")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe: done" in result.stdout
    meta = meta_for(cli, cuts)
    assert meta["duration_s"] == pytest.approx(20.0, abs=0.05)
    assert (meta["width"], meta["height"], meta["fps"]) == (640, 360, 25.0)
    assert meta["audio"] is None
    ws = cli.workspace / video_id(cuts)
    assert (ws / "source.mp4").resolve() == cuts.resolve()

    again = cli("ingest", str(cuts), "--only", "probe")
    assert again.returncode == 0
    assert "probe: skipped" in again.stdout

    forced = cli("ingest", str(cuts), "--only", "probe", "--force")
    assert "probe: done" in forced.stdout


def test_ingest_without_only_runs_probe(cli, test_media):
    result = cli("ingest", str(test_media / "cuts.mp4"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe: done" in result.stdout
    assert f"workspace: {cli.workspace}" in result.stdout


def test_over_cap_rejected(cli, test_media):
    over = test_media / "over_cap.mp4"
    result = cli("ingest", str(over), "--only", "probe")
    assert result.returncode == 2
    assert "longer than 60 minutes" in result.stderr
    # A rejected video leaves no workspace behind.
    assert not (cli.workspace / video_id(over)).exists()


def test_text_file_rejected(cli):
    txt = cli.tmp / "notes.txt"
    txt.write_text("not a video\n")
    result = cli("ingest", str(txt), "--only", "probe")
    assert result.returncode == 2
    assert "no video stream" in result.stderr
    assert not (cli.workspace / video_id(txt)).exists()


def test_audio_only_rejected(cli):
    audio = cli.tmp / "tone.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(audio)],
        check=True,
    )
    result = cli("ingest", str(audio), "--only", "probe")
    assert result.returncode == 2
    assert "no video stream" in result.stderr


def test_video_with_audio_and_odd_name(cli):
    video = cli.tmp / "My Clip.MOV"
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            "-f", "mov", str(video),
        ],
        check=True,
    )  # fmt: skip
    result = cli("ingest", str(video), "--only", "probe")
    assert result.returncode == 0, result.stdout + result.stderr
    meta = meta_for(cli, video)
    assert meta["audio"]["codec"] == "aac"
    assert meta["fps"] == 30.0
    assert (cli.workspace / video_id(video) / "source.mov").exists()


def test_missing_file(cli):
    result = cli("ingest", str(cli.tmp / "nope.mp4"))
    assert result.returncode == 2
    assert "no such file" in result.stderr


def test_same_content_same_workspace(cli, test_media):
    copy = cli.tmp / "copy.mp4"
    shutil.copy(test_media / "cuts.mp4", copy)
    assert cli("ingest", str(test_media / "cuts.mp4")).returncode == 0
    result = cli("ingest", str(copy))
    assert "probe: skipped" in result.stdout
    # The source link now points at the most recent path.
    assert (cli.workspace / video_id(copy) / "source.mp4").resolve() == copy.resolve()
