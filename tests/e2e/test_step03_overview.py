"""Step 3 e2e: `server.cli ingest <video> --only overview` uploads to Gemini and writes
`overview.json`.

Offline mode replays `tests/fixtures/gemini/<media>/overview.json`. The `short`
cases need Video-MME (skip in CI); the `cuts.mp4` cases run everywhere.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.ingest.overview import Overview, parse_clock
from server.workspace import video_id


def ws(cli, video: Path) -> Path:
    return cli.workspace / video_id(video)


def check_short_overview(cli, short: Path) -> None:
    result = cli("ingest", str(short), "--only", "overview")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe: done" in result.stdout
    assert "overview: done (uploaded" in result.stdout
    overview = Overview.model_validate_json((ws(cli, short) / "overview.json").read_text())
    summary = overview.summary.lower()
    assert "mrna" in summary and "vaccin" in summary, overview.summary
    for chapter in overview.chapters:
        assert 0 <= parse_clock(chapter.start) <= parse_clock(chapter.end) <= 84, chapter
    stored = json.loads((ws(cli, short) / "gemini_file.json").read_text())
    assert stored["state"] == "ACTIVE"


def test_short_overview(cli, videomme):
    check_short_overview(cli, videomme("short"))


@pytest.mark.live
def test_short_overview_live(cli, videomme):
    check_short_overview(cli, videomme("short"))


def test_overview_reuses_upload_until_near_expiry(cli, test_media):
    cuts = test_media / "cuts.mp4"
    first = cli("ingest", str(cuts), "--only", "overview")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "overview: done (uploaded, 3 chapters" in first.stdout
    overview = json.loads((ws(cli, cuts) / "overview.json").read_text())
    assert overview["chapters"][-1]["end"] == "00:20"  # the fixture's 00:25 is clamped

    again = cli("ingest", str(cuts), "--only", "overview")
    assert "overview: skipped" in again.stdout

    forced = cli("ingest", str(cuts), "--only", "overview", "--force")
    assert "overview: done (reused upload" in forced.stdout

    # Force the stored expiry into the past: the next run uploads again.
    path = ws(cli, cuts) / "gemini_file.json"
    stored = json.loads(path.read_text())
    stored["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    path.write_text(json.dumps(stored))
    expired = cli("ingest", str(cuts), "--only", "overview", "--force")
    assert expired.returncode == 0, expired.stdout + expired.stderr
    assert "overview: done (uploaded" in expired.stdout
    refreshed = json.loads(path.read_text())
    assert datetime.fromisoformat(refreshed["expires_at"]) > datetime.now(UTC) + timedelta(hours=47)


def test_missing_gemini_key_fails_the_step(cli, test_media):
    result = cli(
        "ingest", str(test_media / "cuts.mp4"), "--only", "overview",
        env={"HARNESS_FAKE_PROVIDERS": "0"},
    )  # fmt: skip
    assert result.returncode == 3
    assert "GEMINI_API_KEY" in result.stderr
