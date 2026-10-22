"""The chats about one video: `workspace/<video_id>/chats/<chat_id>/`.

| File | What it holds |
|---|---|
| `chat.json` | The turns, replayed when the chat is opened again |
| `session.json` | The SDK session id, so the agent resumes the conversation |
| `meta.json` | `title`, `created`, `updated`, and `renamed` once the user named it |

A chat id is a millisecond timestamp in hex plus a random suffix, so ids sort by
creation. A new chat has no folder until its first turn is saved, so chats that
were opened and never used leave nothing behind. `chats()` moves a workspace's
pre-15a `chat.json` and `session.json` into a chat of their own first.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from server.workspace import Workspace

CHATS = "chats"
HISTORY = "chat.json"
SESSION = "session.json"
META = "meta.json"
TITLE_CHARS = 60
ID_PATTERN = re.compile(r"^[0-9a-f]{18}$")

# Meta is read-modified-written from the event loop (turns, titles) and from route
# threads (renames). ponytail: one lock for every chat; per-chat locks if it ever matters.
_meta_lock = threading.Lock()


def new_id() -> str:
    return f"{time.time_ns() // 1_000_000:012x}{secrets.token_hex(3)}"


def valid_id(chat_id: str) -> bool:
    return bool(ID_PATTERN.fullmatch(chat_id))


def short_title(question: str) -> str:
    """The first question on one line, cut at a word to `TITLE_CHARS`."""
    text = " ".join(question.split())
    if len(text) <= TITLE_CHARS:
        return text
    return text[: TITLE_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


@dataclass(frozen=True)
class Chat(Workspace):
    """One chat's folder. It need not exist yet: `save_turn` creates it."""

    @property
    def exists(self) -> bool:
        return self.dir.is_dir()

    def _read(self, name: str, default: Any) -> Any:
        return self.read_json(name) if self.path(name).is_file() else default

    def meta(self) -> dict[str, Any]:
        return self._read(META, {})

    def turns(self) -> list[dict[str, Any]]:
        return self._read(HISTORY, {"turns": []})["turns"]

    def session_id(self) -> str | None:
        return self._read(SESSION, {}).get("session_id")

    def remember_session(self, session_id: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.write_json(SESSION, {"session_id": session_id})

    def save_turn(self, user: dict[str, Any], reply: dict[str, Any]) -> None:
        """Append a user turn and its reply, titling a new chat after the question."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.write_json(HISTORY, {"turns": [*self.turns(), user, reply]})
        now = time.time()
        with _meta_lock:
            meta = self.meta()
            self.write_json(
                META,
                {
                    "title": short_title(user["text"]),
                    "created": now,
                    "renamed": False,
                    **meta,
                    "updated": now,
                },
            )

    def set_title(self, title: str, *, by_user: bool) -> bool:
        """Set the title. One the user chose is never replaced by a generated one."""
        with _meta_lock:
            meta = self.meta()
            if not self.exists or (meta.get("renamed") and not by_user):
                return False
            self.write_json(
                META, {**meta, "title": title, "renamed": meta.get("renamed") or by_user}
            )
            return True

    def summary(self) -> dict[str, Any]:
        meta = self.meta()
        return {"id": self.id, "title": meta.get("title", ""), "updated": meta.get("updated", 0)}


def root(workspace: Workspace) -> Workspace:
    return Workspace(workspace.path(CHATS))


def get(workspace: Workspace, chat_id: str) -> Chat:
    """The chat `chat_id`, which may not exist yet. Raises `ValueError` for a bad id."""
    if not valid_id(chat_id):
        raise ValueError(f"not a chat id: {chat_id!r}")
    return Chat(root(workspace).path(chat_id))


def new(workspace: Workspace) -> Chat:
    return get(workspace, new_id())


def chats(workspace: Workspace) -> list[Chat]:
    """The video's chats, most recently updated first."""
    _migrate(workspace)
    folder = root(workspace).dir
    # No meta.json: a first turn stopped before any output, so nothing to show.
    found = [
        Chat(d)
        for d in (folder.iterdir() if folder.is_dir() else [])
        if valid_id(d.name) and (d / META).is_file()
    ]
    return sorted(found, key=lambda c: (c.meta().get("updated", 0), c.id), reverse=True)


def latest(workspace: Workspace) -> Chat | None:
    found = chats(workspace)
    return found[0] if found else None


def _migrate(workspace: Workspace) -> None:
    """Move a pre-15a `chat.json` / `session.json` into a chat folder of its own."""
    with _meta_lock:  # a page load lists chats from a route thread and the socket at once
        old = [workspace.path(n) for n in (HISTORY, SESSION) if workspace.path(n).is_file()]
        if old:
            _move_into_chat(workspace, old)


def _move_into_chat(workspace: Workspace, old: list) -> None:
    chat = new(workspace)
    chat.dir.mkdir(parents=True)
    for path in old:
        path.replace(chat.path(path.name))
    turns = chat.turns()
    first = next((t["text"] for t in turns if t["role"] == "user"), "")
    updated = max(chat.path(p.name).stat().st_mtime for p in old)
    chat.write_json(
        META,
        {"title": short_title(first) or "Earlier chat", "created": updated, "updated": updated,
         "renamed": False},
    )  # fmt: skip
