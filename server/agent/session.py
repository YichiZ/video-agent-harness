"""One chat session with the agent about one video (plan step 5).

`ChatSession` owns an `AgentClient` (a `ClaudeSDKClient` in production) set up
by `build_options()`: the model from `release.json`, the workspace as `cwd`,
the versioned system prompt, the `video` MCP server and the Skills in
`plugin/skills/` as the only tools, and the budget and turn limits. `ask()`
sends one user message and yields `Event`s as the turn streams. Given a
`server.chats.Chat`, the session keeps the SDK session id and each turn in it, so
`ChatSession(..., chat=chat, resume=True)` continues that conversation; without
one (the evals) nothing is stored.

Offline (`HARNESS_FAKE_PROVIDERS=1`) the client replays the recorded stream
`tests/fixtures/agent/<media>-chat.json`, or `<media>-resume.json` when resuming.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from server.agent.tools import ALLOWED_TOOLS, SERVER_NAME, VideoTools, build_server
from server.auth import claude_env
from server.chats import Chat
from server.config import Release, Settings
from server.providers import AgentClient, fixtures, get_agent_client
from server.tracing import Tracer, Turn
from server.workspace import Workspace

# Largest message the SDK will read from the CLI. A `get_frames` result with 8 JPEGs at
# 720p is several MB of base64, and the SDK's 1 MB default ends the turn with an error.
MAX_BUFFER_BYTES = 32 * 1024 * 1024
PROMPT_DIR = Path(__file__).resolve().parent
# The Skills ship as a local plugin rather than in `.claude/skills/`, so loading them
# needs no `setting_sources` and the repo's CLAUDE.md and settings stay out.
PLUGIN_DIR = PROMPT_DIR / "plugin"
SKILLS = ["video:summarize-video", "video:find-moments"]
# Built-in Claude Code tools the agent must never get, on top of `tools=[]`.
DISALLOWED_TOOLS = ["Bash", "WebFetch", "WebSearch", "Read", "Write", "Edit", "Glob", "Grep"]


def system_prompt(release: Release) -> str:
    path = PROMPT_DIR / f"prompt.{release.prompt_version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no system prompt for prompt_version {release.prompt_version!r}")
    return path.read_text()


def build_options(
    tools: VideoTools, settings: Settings, release: Release, resume: str | None = None
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=release.agent_model,
        cwd=str(tools.workspace.dir),
        system_prompt=system_prompt(release),
        tools=["Skill"],  # the only built-in tool; the rest is the video MCP server
        plugins=[{"type": "local", "path": str(PLUGIN_DIR)}],
        skills=SKILLS,  # hides every other discovered Skill
        mcp_servers={SERVER_NAME: build_server(tools)},
        strict_mcp_config=True,  # no MCP servers from the developer's config
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        permission_mode="dontAsk",
        include_partial_messages=True,
        max_budget_usd=release.max_budget_usd,
        max_turns=release.max_turns,
        max_buffer_size=MAX_BUFFER_BYTES,  # 8 frames at 720p overflow the 1 MB default
        # ...nor their claude.ai connectors (Gmail, Calendar, ...).
        env={**claude_env(settings), "ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
        setting_sources=[],  # ignore the developer's own settings and CLAUDE.md
        resume=resume,
    )


@dataclass(frozen=True)
class Event:
    """One thing to show while a turn streams.

    `kind` is "text" (a streamed text delta), "tool" (a tool call; `text` is the
    tool name without the `mcp__video__` prefix and `input` its arguments),
    "tool_result" (`text` is the result's text, `is_error` set on failure), or
    "result" (the turn ended; `result` holds the SDK's `ResultMessage`). Tool
    calls and their results share `id`.
    """

    kind: str
    text: str = ""
    input: dict | None = None
    result: ResultMessage | None = None
    id: str | None = None
    is_error: bool = False


class ChatSession:
    def __init__(
        self,
        workspace: Workspace,
        settings: Settings,
        release: Release,
        *,
        chat: Chat | None = None,
        resume: bool = False,
        client: AgentClient | None = None,
        fixture: str | None = None,
    ):
        self.workspace = workspace
        self.chat = chat
        self.tools = VideoTools(workspace, settings, release)
        self.session_id = chat.session_id() if resume and chat else None
        if resume and self.session_id is None:
            raise LookupError("no earlier chat to resume for this video")
        self.interrupted = False
        options = build_options(self.tools, settings, release, resume=self.session_id)
        if fixture is None:
            media = fixtures.media_key(workspace.source)
            fixture = f"{media}-{'resume' if resume else 'chat'}"
        self.client = client or get_agent_client(settings, options, fixture=fixture)
        self.tracer = Tracer(settings, release)
        self.last_trace_id: str | None = None

    def _remember(self, session_id: str | None) -> None:
        if session_id and session_id != self.session_id:
            self.session_id = session_id
            if self.chat:
                self.chat.remember_session(session_id)

    async def connect(self) -> None:
        await self.client.connect()

    async def disconnect(self) -> None:
        try:
            await self.client.disconnect()
        finally:  # send this session's traces now; flushing blocks, so off the event loop
            await asyncio.to_thread(self.tracer.flush)

    async def interrupt(self) -> None:
        self.interrupted = True
        await self.client.interrupt()

    async def ask(self, prompt: str) -> AsyncIterator[Event]:
        """One turn: yields its events, saves it to the chat, and traces it in Langfuse.

        The turn is saved even when it is interrupted or abandoned, unless nothing came back.
        """
        turn: Turn | None = None  # started at the first event, once the session id is known
        chat_id = self.chat.id if self.chat else None

        def started() -> Turn:
            nonlocal turn
            if turn is None:
                turn = self.tracer.turn(
                    prompt, video_id=self.workspace.id, session_id=self.session_id, chat_id=chat_id
                )
                self.last_trace_id = turn.trace_id
            return turn

        self.interrupted = False
        answer: list[str] = []
        tools: dict[str | None, dict] = {}
        result: ResultMessage | None = None
        error: str | None = None
        try:
            async with aclosing(self._events(prompt)) as events:
                async for event in events:
                    trace = started()
                    if event.kind == "text":
                        answer.append(event.text)
                    elif event.kind == "tool":
                        trace.tool(event.id, event.text, event.input)
                        tools[event.id] = _tool_call(event)
                    elif event.kind == "tool_result":
                        trace.tool_result(event.id, event.text, event.is_error)
                        if event.id in tools:
                            tools[event.id] = {
                                **tools[event.id],
                                "result": event.text,
                                "is_error": event.is_error,
                            }
                    elif event.kind == "result":
                        result = event.result
                    yield event
        except BaseException as e:  # including an abandoned turn (GeneratorExit, cancel)
            error = f"{type(e).__name__}: {e}"
            raise
        finally:
            started().end("".join(answer), _result_metadata(result, self.session_id), error)
            if self.chat and (answer or tools):  # nothing came back: the client drops it too
                self.chat.save_turn(
                    {"role": "user", "text": prompt, "tools": [], "interrupted": False},
                    {"role": "assistant", "text": "".join(answer), "tools": list(tools.values()),
                     "interrupted": self.interrupted},
                )  # fmt: skip

    async def _events(self, prompt: str) -> AsyncIterator[Event]:
        await self.client.query(prompt)
        streamed = False  # text deltas arrived, so skip the full TextBlock repeat
        async for message in self.client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                self._remember(message.data.get("session_id"))
            elif isinstance(message, StreamEvent):
                delta = message.event.get("delta") or {}
                if message.parent_tool_use_id is None and delta.get("type") == "text_delta":
                    streamed = True
                    yield Event("text", delta["text"])
            elif isinstance(message, AssistantMessage) and message.parent_tool_use_id is None:
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        name = block.name.removeprefix(f"mcp__{SERVER_NAME}__")
                        yield Event("tool", name, input=block.input, id=block.id)
                    elif isinstance(block, TextBlock) and not streamed:
                        yield Event("text", block.text)
                streamed = False
            elif isinstance(message, UserMessage) and message.parent_tool_use_id is None:
                for block in message.content if isinstance(message.content, list) else []:
                    if isinstance(block, ToolResultBlock):
                        yield Event(
                            "tool_result",
                            _result_text(block.content),
                            id=block.tool_use_id,
                            is_error=bool(block.is_error),
                        )
            elif isinstance(message, ResultMessage):
                self._remember(message.session_id)
                yield Event("result", result=message)


def _tool_call(event: Event) -> dict:
    return {"id": event.id, "name": event.text, "input": event.input or {}, "result": None,
            "is_error": False}  # fmt: skip


def _result_metadata(result: ResultMessage | None, session_id: str | None) -> dict:
    if result is None:
        return {"session_id": session_id}
    return {
        "session_id": session_id,
        "subtype": result.subtype,
        "is_error": result.is_error,
        "num_turns": result.num_turns,
        "cost_usd": result.total_cost_usd,
        "duration_ms": result.duration_ms,
        "stop_reason": result.stop_reason,
    }


def _result_text(content: str | list | None) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(
        part.get("text", f"[{part.get('type')}]")
        for part in content or []
        if isinstance(part, dict)
    )
