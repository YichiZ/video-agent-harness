"""One chat session with the agent about one video (plan step 5).

`ChatSession` owns an `AgentClient` (a `ClaudeSDKClient` in production) set up
by `build_options()`: the model from `release.json`, the workspace as `cwd`,
the versioned system prompt, the `video` MCP server as the only tools, and the
budget and turn limits. `ask()` sends one user message and yields `Event`s as
the turn streams. The SDK session id is kept in `session.json`, so
`ChatSession(..., resume=True)` continues the conversation.

Offline (`HARNESS_FAKE_PROVIDERS=1`) the client replays the recorded stream
`tests/fixtures/agent/<media>-chat.json`, or `<media>-resume.json` when resuming.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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
from server.config import Release, Settings
from server.providers import AgentClient, fixtures, get_agent_client
from server.workspace import Workspace

SESSION = "session.json"
PROMPT_DIR = Path(__file__).resolve().parent
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
        tools=[],  # no built-in tools at all; only the video MCP server
        mcp_servers={SERVER_NAME: build_server(tools)},
        strict_mcp_config=True,  # no MCP servers from the developer's config
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        permission_mode="dontAsk",
        include_partial_messages=True,
        max_budget_usd=release.max_budget_usd,
        max_turns=release.max_turns,
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
        resume: bool = False,
        client: AgentClient | None = None,
        fixture: str | None = None,
    ):
        self.workspace = workspace
        self.tools = VideoTools(workspace, settings, release)
        self.session_id = self.stored_session_id() if resume else None
        if resume and self.session_id is None:
            raise LookupError("no earlier chat to resume for this video")
        options = build_options(self.tools, settings, release, resume=self.session_id)
        if fixture is None:
            media = fixtures.media_key(workspace.source)
            fixture = f"{media}-{'resume' if resume else 'chat'}"
        self.client = client or get_agent_client(settings, options, fixture=fixture)

    def stored_session_id(self) -> str | None:
        return self._stored(self.workspace)

    @staticmethod
    def _stored(workspace: Workspace) -> str | None:
        if not workspace.path(SESSION).exists():
            return None
        return workspace.read_json(SESSION).get("session_id")

    @classmethod
    def has_stored_session(cls, workspace: Workspace) -> bool:
        return cls._stored(workspace) is not None

    def _remember(self, session_id: str | None) -> None:
        if session_id and session_id != self.session_id:
            self.session_id = session_id
            self.workspace.write_json(SESSION, {"session_id": session_id})

    async def connect(self) -> None:
        await self.client.connect()

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def interrupt(self) -> None:
        await self.client.interrupt()

    async def ask(self, prompt: str) -> AsyncIterator[Event]:
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


def _result_text(content: str | list | None) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(
        part.get("text", f"[{part.get('type')}]")
        for part in content or []
        if isinstance(part, dict)
    )
