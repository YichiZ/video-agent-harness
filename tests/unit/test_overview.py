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
    assert uploaded and file.uri.startswith("fake://")
    again, uploaded = ensure_active(vision, workspace)
    assert not uploaded and again.name == file.name
    assert len(vision.uploads) == 1


def test_ensure_active_reuploads_a_files_api_record(tmp_path, workspace):
    vision = FakeVisionModel(tmp_path)
    ensure_active(vision, workspace)
    legacy = workspace.read_json(overview.GEMINI_FILE) | {"expires_at": "2026-10-19T00:00:00+00:00"}
    workspace.write_json(overview.GEMINI_FILE, legacy)
    _, uploaded = ensure_active(vision, workspace)
    assert uploaded and len(vision.uploads) == 2
