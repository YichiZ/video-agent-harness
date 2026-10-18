from datetime import UTC, datetime
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, ToolUseBlock

from server.config import REPO_ROOT, Release, Settings, load_release
from server.providers import (
    AgentClient,
    ProviderUnavailable,
    Transcriber,
    VisionModel,
    get_agent_client,
    get_transcriber,
    get_vision_model,
    sdk_codec,
)
from server.providers.fakes import FakeTranscriber, FakeVisionModel, ScriptedAgentClient
from server.providers.fixtures import FixtureMissing

FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def release() -> Release:
    return load_release(REPO_ROOT / "release.json")


def settings(fake: bool, tmp_path: Path, monkeypatch) -> Settings:
    monkeypatch.setenv("HARNESS_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("HARNESS_FAKE_PROVIDERS", "1" if fake else "0")
    monkeypatch.setenv("HARNESS_FIXTURES_DIR", str(FIXTURES))
    return Settings.load()


def test_fakes_satisfy_protocols(tmp_path, monkeypatch, release):
    s = settings(True, tmp_path, monkeypatch)
    assert isinstance(get_transcriber(s, release), Transcriber)
    assert isinstance(get_vision_model(s, release), VisionModel)
    client = get_agent_client(s, ClaudeAgentOptions(), fixture="example")
    assert isinstance(client, AgentClient)


def test_real_providers_not_implemented_yet_say_which_step(tmp_path, monkeypatch, release):
    s = settings(False, tmp_path, monkeypatch)
    # Steps 2 and 3 add the real modules; until then the error names the step.
    for factory, step in [(get_transcriber, "step 2"), (get_vision_model, "step 3")]:
        try:
            factory(s, release)
        except ProviderUnavailable as e:
            assert step in str(e)


def test_fake_transcriber_replays_by_resolved_stem(tmp_path):
    link = tmp_path / "source.mp4"
    target = tmp_path / "example.mp4"
    target.write_bytes(b"")
    link.symlink_to(target)
    fake = FakeTranscriber(FIXTURES)
    assert fake.transcribe(link)["language_code"] == "en"
    assert fake.calls == [link]


def test_fake_transcriber_missing_fixture_says_how_to_record(tmp_path):
    with pytest.raises(FixtureMissing, match="pytest -m live --record"):
        FakeTranscriber(FIXTURES).transcribe(tmp_path / "unknown.mp4")


def test_fake_vision_upload_and_generate(tmp_path):
    fake = FakeVisionModel(FIXTURES)
    remote = fake.upload(tmp_path / "example.mp4")
    assert remote.state == "ACTIVE"
    assert remote.display_name == "example"
    assert remote.expires_at > datetime.now(UTC)
    assert fake.get_file(remote.name) == remote
    out = fake.generate_json(file=remote, prompt="p", schema={}, purpose="overview")
    assert out["summary"] == "An example video."
    assert fake.calls[0]["purpose"] == "overview"
    with pytest.raises(FixtureMissing):
        fake.generate_json(file=remote, prompt="p", schema={}, purpose="captions")


def test_sdk_codec_round_trip():
    msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="mcp__video__search", input={"query": "x"})],
        model="m",
        parent_tool_use_id=None,
    )
    assert sdk_codec.decode(sdk_codec.encode(msg)) == msg


async def test_scripted_agent_replays_turn_and_interrupt():
    client = ScriptedAgentClient.from_fixture(FIXTURES / "agent" / "example.json")
    await client.connect()
    await client.query("What does the virus attach with?")
    messages = [m async for m in client.receive_response()]
    assert isinstance(messages[-1], ResultMessage)
    assert "[00:04]" in messages[-1].result
    assert client.calls == ["What does the virus attach with?"]

    # Interrupting skips straight to the turn's ResultMessage.
    client.turns.append(client.turns[0])
    await client.query("again")
    seen = []
    async for m in client.receive_response():
        seen.append(m)
        await client.interrupt()
    assert len(seen) == 2 and isinstance(seen[-1], ResultMessage)

    with pytest.raises(FixtureMissing):
        await client.query("one too many")
    await client.disconnect()
