import asyncio

import pytest

from server.agent.session import ChatSession, build_options
from server.agent.tools import ALLOWED_TOOLS, VideoTools
from server.config import REPO_ROOT, Settings, load_release
from server.providers.fakes import ScriptedAgentClient
from server.workspace import Workspace


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("HARNESS_ENV_FILE", str(tmp_path / "no.env"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    ws, _ = Workspace.for_video(tmp_path / "ws", video)
    return ws, Settings.load(), load_release()


def test_options_allow_only_video_tools(setup):
    ws, settings, release = setup
    options = build_options(VideoTools(ws, settings, release), settings, release)
    assert options.tools == [] and options.allowed_tools == ALLOWED_TOOLS
    assert "Bash" in options.disallowed_tools and options.permission_mode == "dontAsk"
    assert options.cwd == str(ws.dir) and options.include_partial_messages
    assert options.env == {} and options.setting_sources == []
    assert "[mm:ss]" in options.system_prompt


def test_ask_streams_events_and_stores_session(setup):
    ws, settings, release = setup
    client = ScriptedAgentClient.from_fixture(REPO_ROOT / "tests/fixtures/agent/example.json")
    session = ChatSession(ws, settings, release, client=client)

    async def go():
        await session.connect()
        return [e async for e in session.ask("hi")]

    events = asyncio.run(go())
    kinds = [e.kind for e in events]
    assert kinds[0] == "text" and "tool" in kinds and kinds[-1] == "result"
    tool = next(e for e in events if e.kind == "tool")
    assert (tool.text, tool.input) == ("search", {"query": "spikes"})
    assert ChatSession(ws, settings, release, resume=True, client=client).session_id == (
        "example-session"
    )


def test_resume_needs_a_stored_session(setup):
    with pytest.raises(LookupError):
        ChatSession(*setup, resume=True, client=object())
