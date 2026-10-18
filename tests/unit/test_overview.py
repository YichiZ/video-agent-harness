from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from server.ingest import overview
from server.ingest.overview import Chapter, clamp_chapters, ensure_active, format_clock, parse_clock
from server.providers.fakes import FakeVisionModel
from server.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    ws, _ = Workspace.for_video(tmp_path / "ws", video)
    return ws


def test_clock_round_trip():
    assert parse_clock("01:23") == 83
    assert parse_clock("1:02:03") == 3723
    assert format_clock(83.9) == "01:24"
    assert format_clock(3723) == "1:02:03"


def test_clamp_chapters():
    chapters = [
        Chapter(start="00:00", end="00:10", title="a", summary=""),
        Chapter(start="00:10", end="00:30", title="b", summary=""),
        Chapter(start="00:40", end="00:50", title="c", summary=""),
    ]
    kept = clamp_chapters(chapters, 20.5)
    assert [(c.start, c.end) for c in kept] == [("00:00", "00:10"), ("00:10", "00:20")]


def test_ensure_active_uploads_then_reuses(tmp_path, workspace):
    vision = FakeVisionModel(tmp_path)
    file, uploaded = ensure_active(vision, workspace)
    assert uploaded and file.state == "ACTIVE"
    again, uploaded = ensure_active(vision, workspace)
    assert not uploaded and again.name == file.name
    assert len(vision.uploads) == 1


def test_ensure_active_reuploads_within_an_hour_of_expiry(tmp_path, workspace):
    vision = FakeVisionModel(tmp_path)
    file, _ = ensure_active(vision, workspace)
    near_expiry = file.expires_at - timedelta(minutes=59)
    _, uploaded = ensure_active(vision, workspace, now=near_expiry)
    assert uploaded and len(vision.uploads) == 2


class SlowVision(FakeVisionModel):
    """Uploads start PROCESSING and become `final` on the second poll."""

    final = "ACTIVE"

    def upload(self, media):
        self.polls = 0
        return replace(super().upload(media), state="PROCESSING")

    def get_file(self, name):
        self.polls += 1
        state = "PROCESSING" if self.polls < 2 else self.final
        return replace(super().get_file(name), state=state)


def test_ensure_active_polls_until_active(tmp_path, workspace, monkeypatch):
    monkeypatch.setattr(overview.time, "sleep", lambda _: None)
    vision = SlowVision(tmp_path)
    file, _ = ensure_active(vision, workspace)
    assert file.state == "ACTIVE" and vision.polls == 2
    assert workspace.read_json(overview.GEMINI_FILE)["state"] == "ACTIVE"


def test_ensure_active_raises_when_processing_fails(tmp_path, workspace, monkeypatch):
    monkeypatch.setattr(overview.time, "sleep", lambda _: None)
    vision = SlowVision(tmp_path)
    vision.final = "FAILED"
    with pytest.raises(RuntimeError, match="could not process"):
        ensure_active(vision, workspace)
    assert not workspace.path(overview.GEMINI_FILE).exists()


def test_stored_expiry_is_timezone_aware(tmp_path, workspace):
    ensure_active(FakeVisionModel(tmp_path), workspace)
    stored = overview.stored_file(workspace)
    assert stored.expires_at > datetime.now(UTC)
