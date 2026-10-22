"""Langfuse tracing (plan step 13): one trace per chat turn.

A turn's trace is an `agent` observation named "chat-turn" with the prompt as
input and the answer as output, a `tool` child per tool call, and the cost,
turn count and stop reason in its metadata. Every trace carries the release
hash (`release_hash()`, the first 12 hex characters of `release.json`'s SHA-256)
as its version and as a tag, next to the prompt version, so a regression can be
pinned to the config that caused it. The chat's SDK session id is the Langfuse
session id.

Tracing is on when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set (in
the environment or `.env`; `LANGFUSE_BASE_URL` picks the region), and off with
fake providers. Otherwise every call is a no-op, and a tracing failure is
logged, never raised: a trace is never worth a failed answer. Traces carry the
prompts, answers and tool inputs and outputs (transcript and caption text) as they
are; `ChatSession.disconnect()` flushes them.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from server.config import Release, Settings, release_path

log = logging.getLogger(__name__)


def release_hash(path=None) -> str:
    return hashlib.sha256((path or release_path()).read_bytes()).hexdigest()[:12]


class Turn:
    """One traced chat turn. With tracing off, `trace_id` is None and nothing is sent."""

    def __init__(self, root: Any | None):
        self._root = root
        self._tools: dict[str | None, Any] = {}
        self.trace_id: str | None = getattr(root, "trace_id", None)

    def _safely(self, what: str, fn) -> None:
        if self._root is None:
            return
        try:
            fn()
        except Exception:
            log.exception("langfuse: %s failed", what)

    def tool(self, tool_id: str | None, name: str, args: dict | None) -> None:
        def start():
            self._tools[tool_id] = self._root.start_observation(
                name=name, as_type="tool", input=args or {}
            )

        self._safely("tool span", start)

    def tool_result(self, tool_id: str | None, text: str, is_error: bool) -> None:
        span = self._tools.pop(tool_id, None)
        if span is not None:
            self._safely(
                "tool result",
                lambda: span.update(output=text, level="ERROR" if is_error else None).end(),
            )

    def end(self, output: str, metadata: dict[str, Any], error: str | None = None) -> None:
        def finish():
            for span in self._tools.values():  # interrupted mid-call
                span.end()
            self._root.update(
                output=output,
                metadata=metadata,
                level="ERROR" if error else None,
                status_message=error,
            ).end()

        self._safely("turn end", finish)


class Tracer:
    def __init__(self, settings: Settings, release: Release):
        self.release = release
        self.version = release_hash()
        self._client = None
        if settings.fake_providers or not (
            settings.langfuse_public_key and settings.langfuse_secret_key
        ):
            return
        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                base_url=settings.langfuse_base_url,
                release=self.version,
            )
        except Exception:
            log.exception("langfuse: client setup failed; tracing is off")

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def turn(
        self,
        prompt: str,
        *,
        video_id: str,
        session_id: str | None,
        chat_id: str | None = None,
        tags=(),
    ) -> Turn:
        if self._client is None:
            return Turn(None)
        try:
            from langfuse import propagate_attributes

            with propagate_attributes(
                session_id=session_id,
                version=self.version,
                tags=[self.version, f"prompt-{self.release.prompt_version}", *tags],
                trace_name="chat-turn",
                metadata={"video_id": video_id, "chat_id": chat_id or ""},
            ):
                root = self._client.start_observation(
                    name="chat-turn",
                    as_type="agent",
                    input=prompt,
                    version=self.version,
                    metadata={"agent_model": self.release.agent_model},
                )
            return Turn(root)
        except Exception:
            log.exception("langfuse: trace start failed")
            return Turn(None)

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception:
                log.exception("langfuse: flush failed")
