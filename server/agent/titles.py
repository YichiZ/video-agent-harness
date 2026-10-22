"""A short chat title from the first question and answer, written by `release.title_model`.

One Agent SDK `query()` with no tools and one turn, so it works with an API key
and with the subscription login alike. Offline (`HARNESS_FAKE_PROVIDERS=1`) it
returns `FAKE_TITLE`.
"""

from __future__ import annotations

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from server.auth import claude_env
from server.chats import short_title
from server.config import Release, Settings

FAKE_TITLE = "Fake chat title"
MAX_WORDS = 6
ANSWER_CHARS = 2000  # the start of the answer says enough about the topic
PROMPT = (
    "Write a title of at most six words for a chat about a video that starts with this "
    "question and answer. Reply with the title only, with no quotes and no final period."
)


async def write_title(question: str, answer: str, settings: Settings, release: Release) -> str:
    if settings.fake_providers:
        return FAKE_TITLE
    options = ClaudeAgentOptions(
        model=release.title_model,
        system_prompt=PROMPT,
        tools=[],
        strict_mcp_config=True,
        max_turns=1,
        env={**claude_env(settings), "ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
        setting_sources=[],
    )
    prompt = f"Question: {question}\n\nAnswer: {answer[:ANSWER_CHARS]}"
    parts = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            parts += [b.text for b in message.content if isinstance(b, TextBlock)]
    return clean("".join(parts))


def clean(text: str) -> str:
    """One line, no quotes or final period, at most `MAX_WORDS` words. Raises if empty."""
    words = text.strip().strip("\"'“”").rstrip(".").split()[:MAX_WORDS]
    if not words:
        raise ValueError("the title model returned no text")
    return short_title(" ".join(words))
