"""Provider protocols.

Every external model sits behind one of these small interfaces, so ingest steps,
tools and the agent session never import a vendor SDK directly. Offline tests
swap in the fakes from `server.providers.fakes`.

The protocols deliberately pass provider responses through as plain JSON
(`dict`). The step that owns an output (for example step 2 for
`transcript.json`) parses the raw response into its own schema. That keeps
recorded fixtures identical to what the real API returned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from claude_agent_sdk import Message

JSON = dict[str, Any]


@runtime_checkable
class Transcriber(Protocol):
    """Speech to text (ElevenLabs Scribe v2 in production, step 2)."""

    def transcribe(self, media: Path) -> JSON:
        """Transcribe an audio or video file.

        Returns the raw Scribe response as JSON: `language_code`, `text` and
        `words[]` with `text`, `start`, `end`, `type` and `speaker_id`.
        """
        ...


@dataclass(frozen=True)
class RemoteFile:
    """A video uploaded to the vision provider (the Gemini Files API)."""

    name: str
    display_name: str  # the media key (source stem); names recorded fixtures
    uri: str
    mime_type: str
    state: str  # "PROCESSING", "ACTIVE" or "FAILED"
    expires_at: datetime


@runtime_checkable
class VisionModel(Protocol):
    """Video understanding (Gemini in production, steps 3, 4 and 7)."""

    def upload(self, media: Path) -> RemoteFile:
        """Upload a video. The returned file may still be `PROCESSING`."""
        ...

    def get_file(self, name: str) -> RemoteFile:
        """Fetch a file's current state, to poll until it is `ACTIVE`."""
        ...

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
        """Ask about the video and get JSON that matches `schema`.

        `purpose` names the call ("overview", "captions", "inspect"). It is
        used for logging and to name recorded fixtures. `start`, `end` (seconds)
        and `fps` restrict the request to a time range of the video.
        """
        ...


@runtime_checkable
class AgentClient(Protocol):
    """The subset of `claude_agent_sdk.ClaudeSDKClient` the harness uses (step 5).

    `ClaudeSDKClient` satisfies it as is. The fake replays recorded
    `claude_agent_sdk` messages, so session code handles one set of types.
    """

    async def connect(self) -> None: ...

    async def query(self, prompt: str, session_id: str = "default") -> None: ...

    def receive_response(self) -> AsyncIterator[Message]:
        """Yield messages until and including the turn's `ResultMessage`."""
        ...

    async def interrupt(self) -> None: ...

    async def disconnect(self) -> None: ...
