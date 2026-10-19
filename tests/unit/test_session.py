import asyncio
import json

import pytest

from server.agent.session import PLUGIN_DIR, SKILLS, ChatSession, build_options
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
    assert options.tools == ["Skill"] and options.allowed_tools == ALLOWED_TOOLS
    assert "Bash" in options.disallowed_tools and options.permission_mode == "dontAsk"
    assert options.cwd == str(ws.dir) and options.include_partial_messages
    assert "ANTHROPIC_API_KEY" not in options.env and options.setting_sources == []
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


def test_options_shut_out_other_mcp_servers(setup):
    ws, settings, release = setup
    options = build_options(VideoTools(ws, settings, release), settings, release)
    assert options.strict_mcp_config
    assert options.env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"


@pytest.mark.parametrize("name", ["TbaCxIJ_VP4-chat", "TbaCxIJ_VP4-resume"])
def test_recorded_sessions_only_had_video_tools(name):
    data = json.loads((REPO_ROOT / f"tests/fixtures/agent/{name}.json").read_text())
    init = data["turns"][0][0]
    assert init["subtype"] == "init"
    assert init["data"]["tools"] and all(
        t.startswith("mcp__video__") for t in init["data"]["tools"]
    )
    assert [s["name"] for s in init["data"]["mcp_servers"]] == ["video"]
    assert "/Users/" not in json.dumps(data)


def test_skills_load_from_the_plugin_without_setting_sources(setup):
    ws, settings, release = setup
    options = build_options(VideoTools(ws, settings, release), settings, release)
    assert options.setting_sources == [] and options.skills == SKILLS
    assert options.plugins == [{"type": "local", "path": str(PLUGIN_DIR)}]
    manifest = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text())
    for name in SKILLS:
        plugin, skill = name.split(":")
        assert plugin == manifest["name"]
        assert (PLUGIN_DIR / "skills" / skill / "SKILL.md").is_file()


def test_options_allow_large_frame_results(setup):
    ws, settings, release = setup
    options = build_options(VideoTools(ws, settings, release), settings, release)
    assert options.max_buffer_size >= 8 * 1024 * 1024  # 8 frames at 720p, base64
