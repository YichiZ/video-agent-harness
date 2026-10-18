"""Fake and recording providers.

- `FakeTranscriber`, `FakeVisionModel` and `ScriptedAgentClient` replay recorded
  responses from the fixtures dir and need no keys or network. They are what
  `HARNESS_FAKE_PROVIDERS=1` selects.
- `RecordingTranscriber`, `RecordingVisionModel` and `RecordingAgentClient` wrap a
  real provider and write every response to the fixtures dir. They are what
  `HARNESS_RECORD_FIXTURES=1` selects.

Each fake keeps a `calls` list so tests can assert what was (or was not) called.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from claude_agent_sdk import Message, ResultMessage

from server.providers import fixtures, sdk_codec
from server.providers.base import JSON, AgentClient, RemoteFile, Transcriber, VisionModel

GEMINI_FILE_TTL = timedelta(hours=48)


class FakeTranscriber:
    def __init__(self, fixtures_dir: Path):
        self.fixtures_dir = fixtures_dir
        self.calls: list[Path] = []

    def transcribe(self, media: Path) -> JSON:
        self.calls.append(media)
        return fixtures.read(fixtures.scribe_path(self.fixtures_dir, media))


class RecordingTranscriber:
    def __init__(self, inner: Transcriber, fixtures_dir: Path):
        self.inner = inner
        self.fixtures_dir = fixtures_dir

    def transcribe(self, media: Path) -> JSON:
        response = self.inner.transcribe(media)
        fixtures.write(fixtures.scribe_path(self.fixtures_dir, media), response)
        return response


def _request(prompt: str, schema: JSON, start, end, fps) -> dict[str, Any]:
    return {"prompt": prompt, "schema": schema, "start": start, "end": end, "fps": fps}


@dataclass
class FakeVisionModel:
    """Replays Gemini responses. Uploads are instant and become ACTIVE at once."""

    fixtures_dir: Path
    ttl: timedelta = GEMINI_FILE_TTL
    calls: list[dict[str, Any]] = field(default_factory=list)
    uploads: list[Path] = field(default_factory=list)
    _files: dict[str, RemoteFile] = field(default_factory=dict)

    def upload(self, media: Path) -> RemoteFile:
        self.uploads.append(media)
        key = fixtures.media_key(media)
        name = f"files/fake-{key}-{len(self.uploads)}"
        remote = RemoteFile(
            name=name,
            display_name=key,
            uri=f"fake://{name}",
            mime_type="video/mp4",
            state="ACTIVE",
            expires_at=datetime.now(UTC) + self.ttl,
        )
        self._files[name] = remote
        return remote

    def get_file(self, name: str) -> RemoteFile:
        try:
            return self._files[name]
        except KeyError:
            raise LookupError(f"fake vision model has no file {name!r}") from None

    def generate_json(
        self,
        *,
        file: RemoteFile,
        prompt: str,
        schema: JSON,
        purpose: str,
        start: float | None = None,
        end: float | None = None,
        fps: float | None = None,
    ) -> JSON:
        request = _request(prompt, schema, start, end, fps)
        self.calls.append({"purpose": purpose, "file": file.name, **request})
        digest = fixtures.request_hash(**request)
        candidates = fixtures.gemini_paths(self.fixtures_dir, file.display_name, purpose, digest)
        for path in candidates:
            if path.is_file():
                return fixtures.read(path)
        return fixtures.read(candidates[0])  # raises FixtureMissing naming the exact path


class RecordingVisionModel:
    def __init__(self, inner: VisionModel, fixtures_dir: Path):
        self.inner = inner
        self.fixtures_dir = fixtures_dir

    def upload(self, media: Path) -> RemoteFile:
        return self.inner.upload(media)

    def get_file(self, name: str) -> RemoteFile:
        return self.inner.get_file(name)

    def generate_json(self, *, file, prompt, schema, purpose, start=None, end=None, fps=None):
        response = self.inner.generate_json(
            file=file, prompt=prompt, schema=schema, purpose=purpose, start=start, end=end, fps=fps
        )
        digest = fixtures.request_hash(**_request(prompt, schema, start, end, fps))
        path = fixtures.gemini_paths(self.fixtures_dir, file.display_name, purpose, digest)[0]
        fixtures.write(path, response)
        return response


class ScriptedAgentClient:
    """Replays a recorded SDK message stream, one recorded turn per `query`.

    Prompts are recorded in `calls` but do not select the turn: turn N of the
    script answers the Nth query. `interrupt()` ends the current turn early with
    the turn's `ResultMessage`, as the real client does.
    """

    def __init__(self, turns: list[list[Message]]):
        self.turns = turns
        self.calls: list[str] = []
        self.connected = False
        self.interrupted = False
        self._pending: list[Message] | None = None

    @classmethod
    def from_fixture(cls, path: Path) -> ScriptedAgentClient:
        data = fixtures.read(path)
        return cls([sdk_codec.decode(turn) for turn in data["turns"]])

    async def connect(self) -> None:
        self.connected = True

    async def query(self, prompt: str, session_id: str = "default") -> None:
        if not self.connected:
            raise RuntimeError("query() before connect()")
        turn = len(self.calls)
        self.calls.append(prompt)
        if turn >= len(self.turns):
            raise fixtures.FixtureMissing(
                f"scripted agent has {len(self.turns)} turns, got query #{turn + 1}: {prompt!r}"
            )
        self._pending = list(self.turns[turn])
        self.interrupted = False

    async def receive_response(self) -> AsyncIterator[Message]:
        pending, self._pending = self._pending or [], None
        for message in pending:
            if self.interrupted and not isinstance(message, ResultMessage):
                continue
            yield message
            if isinstance(message, ResultMessage):
                return

    async def interrupt(self) -> None:
        self.interrupted = True

    async def disconnect(self) -> None:
        self.connected = False


class RecordingAgentClient:
    """Wraps a real client and writes every turn to `agent/<name>.json` on disconnect."""

    def __init__(self, inner: AgentClient, path: Path):
        self.inner = inner
        self.path = path
        self.turns: list[list[Message]] = []

    async def connect(self) -> None:
        await self.inner.connect()

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.turns.append([])
        await self.inner.query(prompt, session_id)

    async def receive_response(self) -> AsyncIterator[Message]:
        async for message in self.inner.receive_response():
            self.turns[-1].append(message)
            yield message

    async def interrupt(self) -> None:
        await self.inner.interrupt()

    async def disconnect(self) -> None:
        await self.inner.disconnect()
        fixtures.write(self.path, {"turns": [sdk_codec.encode(t) for t in self.turns]})
