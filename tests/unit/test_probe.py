import pytest

from server.ingest import Rejected
from server.ingest.probe import build_meta, format_clock, parse_rate


def probe(duration="83.9", **video):
    stream = {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1280,
              "height": 720, "avg_frame_rate": "30000/1001", **video}  # fmt: skip
    return {"format": {"duration": duration, "size": "100"}, "streams": [stream]}


@pytest.mark.parametrize(
    ("rate", "expected"),
    [("30000/1001", 29.97), ("25/1", 25.0), ("0/0", None), (None, None), ("x", None)],
)
def test_parse_rate(rate, expected):
    assert parse_rate(rate) == expected


def test_format_clock():
    assert format_clock(83.9) == "1:23"
    assert format_clock(3660) == "1:01:00"


def test_cap_is_inclusive():
    assert build_meta(probe(duration="3600"), max_seconds=3600)["duration_s"] == 3600
    with pytest.raises(Rejected, match="longer than 60 minutes"):
        build_meta(probe(duration="3600.5"), max_seconds=3600)


def test_cover_art_is_not_a_video_stream():
    p = probe(disposition={"attached_pic": 1})
    with pytest.raises(Rejected, match="no video stream"):
        build_meta(p, max_seconds=3600)


def test_rotation_swaps_display_size():
    meta = build_meta(probe(side_data_list=[{"rotation": -90}]), max_seconds=3600)
    assert (meta["width"], meta["height"], meta["rotation"]) == (720, 1280, 270)


def test_falls_back_to_stream_duration_and_r_frame_rate():
    p = probe(duration=None, avg_frame_rate="0/0", r_frame_rate="24/1")
    p["streams"][0]["duration"] = "12.5"
    meta = build_meta(p, max_seconds=3600)
    assert (meta["duration_s"], meta["fps"]) == (12.5, 24.0)
