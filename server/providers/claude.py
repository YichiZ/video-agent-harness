"""Small helpers around the Claude Agent SDK that are not part of a chat session."""

from __future__ import annotations

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

from server.auth import claude_env
from server.config import Release, Settings


async def ping(settings: Settings, release: Release) -> str:
    """Make one tiny Claude call in the detected auth mode. Returns the reply text.

    Raises `RuntimeError` if the call fails, so `doctor --live` can report it.
    """
    options = ClaudeAgentOptions(
        model=release.agent_model,
        system_prompt="You are a health check. Reply with the single word OK.",
        tools=[],
        allowed_tools=[],
        permission_mode="dontAsk",
        max_turns=1,
        env=claude_env(settings),
        setting_sources=[],
    )
    text, result = "", None
    async for message in query(prompt="ping", options=options):
        if isinstance(message, AssistantMessage):
            text += "".join(b.text for b in message.content if isinstance(b, TextBlock))
        elif isinstance(message, ResultMessage):
            result = message
    if result is None or result.is_error:
        detail = (result.result or result.subtype) if result else "no result message"
        raise RuntimeError(f"Claude call failed: {detail}")
    return text.strip()
