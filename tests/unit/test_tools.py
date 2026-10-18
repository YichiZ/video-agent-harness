import asyncio
import json
import subprocess

import pytest
from mcp import Client

from server.agent.tools import ALLOWED_TOOLS, ToolError, VideoTools, build_server
from server.config import Settings, load_release
from server.providers.fakes import FakeVisionModel
from server.workspace import Workspace

SEGMENTS = [
    {"id": 0, "start": 0.5, "end": 4.0, "speaker": "s0", "text": " The virus attaches its spikes."},
    {
        "id": 1,
        "start": 4.5,
        "end": 9.0,
        "speaker": "s0",
        "text": "Spikes are a target for vaccines.",
    },
    {"id": 2, "start": 12.0, "end": 18.0, "speaker": "s1", "text": "Cells make copies."},
]


@pytest.fixture
def tools(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=20",
         "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )  # fmt: skip
    ws, _ = Workspace.for_video(tmp_path / "ws", video)
    ws.write_json("meta.json", {"duration_s": 20.0})
    ws.write_json("transcript.json", {"segments": SEGMENTS})
    ws.write_json("overview.json", {"summary": "A test video.", "subjects": [], "chapters": []})
    (tmp_path / "gemini" / "clip").mkdir(parents=True)
    (tmp_path / "gemini" / "clip" / "inspect.json").write_text(
        json.dumps(
            {"answer": "Bars [00:02].", "observations": [{"time": "00:02", "observation": "x"}]}
        )
    )
    monkeypatch.setenv("HARNESS_FIXTURES_DIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_FAKE_PROVIDERS", "1")
    release = load_release().model_copy(update={"inspect_limit": 2})
    t = VideoTools(ws, Settings.load(), release)
    t._vision = FakeVisionModel(tmp_path)
    return t


def test_overview_adds_duration(tools):
    assert tools.get_overview()["duration"] == "00:20"


def test_search_ranks_phrase_then_time_and_respects_k(tools):
    hits = tools.search("its spikes", k=2)["hits"]
    assert [h["start"] for h in hits] == [0.5, 4.5]
    assert hits[0]["text"] == "The virus attaches its spikes."
    assert tools.search("SPIKES")["hits"][0]["time"] == "00:00"
    assert tools.search("elephant")["hits"] == []
    with pytest.raises(ToolError):
        tools.search("  ")


def test_transcript_range_overlaps_and_clamps(tools):
    assert [s["start"] for s in tools.get_transcript(4.2, 10)["segments"]] == [4.5]
    whole = tools.get_transcript(-5, 999)
    assert (whole["start"], whole["end"]) == (0.0, 20.0)
    with pytest.raises(ToolError, match="empty time range"):
        tools.get_transcript(30, 40)


def test_inspect_caches_and_limits(tools):
    first = tools.inspect("what is shown?", 0, 999)
    assert first["cached"] is False and first["end"] == 20.0
    assert tools.inspect("what is shown?", 0, 20)["cached"] is True  # clamped args hit the cache
    assert len(tools._vision.calls) == 1
    assert tools._vision.calls[0]["start"] == 0.0 and tools._vision.calls[0]["end"] == 20.0
    tools.inspect("another?", 0, 5)
    with pytest.raises(ToolError, match="inspect limit reached"):
        tools.inspect("third?", 0, 5)


def test_frames_are_jpegs_and_capped(tools):
    data, frames = tools.get_frames([5, 99])
    assert [f["t"] for f in data["frames"]] == [5.0, 19.95]
    assert all(f[:2] == b"\xff\xd8" and f[-2:] == b"\xff\xd9" for f in frames)
    with pytest.raises(ToolError, match="at most 8"):
        tools.get_frames(list(range(9)))


def test_mcp_server_lists_tools_and_reports_errors(tools):
    async def go():
        async with Client(build_server(tools)["instance"]) as client:
            listed = await client.list_tools()
            bad = await client.call_tool("get_frames", {"timestamps": list(range(9))})
            ok = await client.call_tool("get_frames", {"timestamps": [1]})
        return listed, bad, ok

    listed, bad, ok = asyncio.run(go())
    assert sorted(f"mcp__video__{t.name}" for t in listed.tools) == sorted(ALLOWED_TOOLS)
    assert bad.is_error and "at most 8" in bad.content[0].text
    assert [b.type for b in ok.content] == ["text", "image"]


def test_list_shots_prefers_matching_captions_and_filters_range(tools, monkeypatch):
    shots = [{"index": i, "start_s": i * 5.0, "end_s": i * 5.0 + 5} for i in range(4)]
    tools.workspace.write_json("shots.json", {"shots": shots})
    early = tools.list_shots(4, 6)
    assert [s["index"] for s in early["shots"]] == [0, 1] and "not ready" in early["note"]

    captioned = [{**s, "caption": f"Shot {s['index']}."} for s in shots]
    tools.workspace.write_json("captions.json", {"shots": captioned})
    late = tools.list_shots(16)
    assert [s["caption"] for s in late["shots"]] == ["Shot 3."] and "note" not in late

    tools.workspace.write_json("shots.json", {"shots": shots[:3]})  # shots re-detected
    stale = tools.list_shots()
    assert "caption" not in stale["shots"][0] and "out of date" in stale["note"]

    monkeypatch.setattr("server.agent.tools.MAX_SHOTS", 2)
    assert len(tools.list_shots()["shots"]) == 2 and "narrow" in tools.list_shots()["note"]
