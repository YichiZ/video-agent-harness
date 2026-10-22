"""The `WS /chat` frames. `scripts/export_protocol.py` writes them to `web/src/protocol.ts`.

Client to server: `UserMessage`, `Interrupt`.
Server to client: `History` (on connect: the open chat and its turns so far),
`TextDelta`, `ToolUse`, `ToolResult`, `Result` (one per turn), `ChatTitle` (once
a chat's generated title is ready), `IngestStatus` (on connect and whenever
`status.json` changes) and `ChatError`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter


class UserMessage(BaseModel):
    type: Literal["user_message"] = "user_message"
    text: str = Field(min_length=1, max_length=20_000)


class Interrupt(BaseModel):
    type: Literal["interrupt"] = "interrupt"


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolUse(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str | None
    name: str
    input: dict[str, Any]


class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    id: str | None
    text: str
    is_error: bool


class Result(BaseModel):
    type: Literal["result"] = "result"
    subtype: str
    is_error: bool
    num_turns: int
    cost_usd: float | None
    interrupted: bool


class IngestStatus(BaseModel):
    type: Literal["ingest_status"] = "ingest_status"
    video_id: str | None
    generation: int = 0
    """Counts uploads since the server started, so a re-upload of the same video shows."""
    name: str | None
    chat_ready: bool
    steps: dict[str, str]
    """Step name to state: "pending", "running", "done", "failed", "blocked" or "cancelled"."""
    errors: dict[str, str] = {}
    """Why a failed or blocked step did not finish."""


class ToolCall(BaseModel):
    id: str | None
    name: str
    input: dict[str, Any]
    result: str | None = None
    is_error: bool = False


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    tools: list[ToolCall] = []
    interrupted: bool = False


class History(BaseModel):
    type: Literal["history"] = "history"
    video_id: str | None
    generation: int
    """The upload count; `IngestStatus.generation` moving past it means a new upload."""
    chat_id: str | None
    """The open chat; `None` only without a video. It has no folder until its first turn."""
    turns: list[ChatTurn]


class ChatTitle(BaseModel):
    type: Literal["chat_title"] = "chat_title"
    chat_id: str
    title: str


class ChatError(BaseModel):
    type: Literal["error"] = "error"
    message: str
    refused: bool = False
    """The user message was refused (no turn started), rather than a turn failing."""


ClientFrame = Annotated[UserMessage | Interrupt, Field(discriminator="type")]
ServerFrame = (
    History | TextDelta | ToolUse | ToolResult | Result | ChatTitle | IngestStatus | ChatError
)
client_frame = TypeAdapter(ClientFrame)
