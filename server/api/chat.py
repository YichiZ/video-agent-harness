"""`WS /chat?chat=<id>`: one chat about the current video, over a WebSocket.

`chat` picks the chat: an id opens it (an empty one if it has no folder yet),
`new` starts a new one, and without it the most recently updated chat opens, or
a new one. The connection first sends a `History` frame naming the chat with its
turns, then keeps one `ChatSession` for it that resumes the chat's stored session.
Client frames are read in one task and every server frame goes through one queue,
so a turn can stream while an `interrupt` arrives and `ingest_status` frames go
out whenever `status.json` changes.

A chat runs one turn at a time, across connections (`Library.busy`); different
chats run at once, each in its own Claude Code process. After a chat's first
answer its title is written in the background and pushed as a `ChatTitle` frame.
Once a new upload changes the current video, the connection refuses messages and
the client reconnects to that video's chats.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import aclosing

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from server import chats
from server.agent import titles
from server.agent.session import ChatSession, Event
from server.api import protocol as p
from server.api.library import Library
from server.auth import AuthMode, detect_auth
from server.chats import Chat

STATUS_POLL_S = 0.5

log = logging.getLogger(__name__)
_titling: set[asyncio.Task] = set()  # title tasks outlive their connection


def load_history(chat: Chat | None) -> list[p.ChatTurn]:
    return [p.ChatTurn.model_validate(t) for t in chat.turns()] if chat else []


def open_chat(library: Library, requested: str | None) -> Chat | None:
    """The chat `requested` names, for the current video. Raises `ValueError` for a bad id."""
    current = library.current
    if current is None:
        return None
    if requested == "new":
        return chats.new(current)
    if requested:
        return chats.get(current, requested)
    return chats.latest(current) or chats.new(current)


def frame_for(event: Event, interrupted: bool) -> BaseModel | None:
    if event.kind == "text":
        return p.TextDelta(text=event.text)
    if event.kind == "tool":
        return p.ToolUse(id=event.id, name=event.text, input=event.input or {})
    if event.kind == "tool_result":
        return p.ToolResult(id=event.id, text=event.text, is_error=event.is_error)
    if event.kind == "result" and event.result:
        r = event.result
        return p.Result(
            subtype=r.subtype,
            is_error=r.is_error,
            num_turns=r.num_turns,
            cost_usd=r.total_cost_usd,
            interrupted=interrupted,
        )
    return None


class Connection:
    def __init__(self, ws: WebSocket, library: Library, chat: Chat | None):
        self.ws = ws
        self.library = library
        self.chat = chat
        self.generation = library.generation  # the upload the chat belongs to
        self.outbox: asyncio.Queue[BaseModel] = asyncio.Queue()
        self.session: ChatSession | None = None
        self.turn: asyncio.Task | None = None

    def send(self, frame: BaseModel) -> None:
        self.outbox.put_nowait(frame)

    def refuse(self, message: str) -> None:
        """No turn started for the user's message."""
        self.send(p.ChatError(message=message, refused=True))

    async def run(self) -> None:
        current = self.library.current
        self.send(
            p.History(
                video_id=current.id if current else None,
                generation=self.generation,
                chat_id=self.chat.id if self.chat else None,
                turns=load_history(self.chat),
            )
        )
        tasks = [asyncio.create_task(c) for c in (self._writer(), self._status())]
        try:
            await self._reader()
        except WebSocketDisconnect:
            pass
        finally:
            tasks += [self.turn] if self.turn else []
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)  # let the turn unwind first
            await self._close_session()

    async def _writer(self) -> None:
        while True:
            frame = await self.outbox.get()
            await self.ws.send_text(frame.model_dump_json())

    async def _status(self) -> None:
        last = None
        while True:
            status = self.library.status()
            if status != last:
                self.send(status)
                last = status
            await asyncio.sleep(STATUS_POLL_S)

    async def _reader(self) -> None:
        while True:
            raw = await self.ws.receive_text()
            try:
                frame = p.client_frame.validate_json(raw)
            except ValidationError as e:
                self.send(p.ChatError(message=f"bad frame: {e.errors()[0]['msg']}"))
                continue
            if isinstance(frame, p.Interrupt):
                if self.turn and not self.turn.done() and self.session:
                    await self.session.interrupt()
            else:
                await self._start_turn(frame.text)

    async def _start_turn(self, text: str) -> None:
        status = self.library.status()
        if not status.chat_ready:
            self.refuse("the video is not chat-ready yet")
            return
        if self.chat is None or self.library.generation != self.generation:
            self.refuse("a new video was uploaded; reopen the chat")
            return
        settings = self.library.settings
        if detect_auth(settings).mode is AuthMode.NONE and not settings.fake_providers:
            self.refuse("no Claude auth: set ANTHROPIC_API_KEY or log in")
            return
        busy = str(self.chat.dir)
        if busy in self.library.busy:
            self.refuse("a turn is already running; interrupt it first")
            return
        self.library.busy.add(busy)  # before the await, so another tab can't slip in
        try:
            await self._ensure_session()
        except Exception as e:
            self.library.busy.discard(busy)
            log.exception("could not start a chat session")
            self.refuse(f"could not start the chat: {type(e).__name__}: {e}")
            return
        self.turn = asyncio.create_task(self._turn(text))
        self.turn.add_done_callback(lambda _: self.library.busy.discard(busy))  # even if cancelled

    async def _ensure_session(self) -> None:
        if self.session:
            return
        assert self.chat and self.library.current
        session = ChatSession(
            self.library.current,
            self.library.settings,
            self.library.release,
            chat=self.chat,
            resume=self.chat.session_id() is not None,
        )
        await session.connect()
        self.session = session

    async def _turn(self, text: str) -> None:
        assert self.session and self.chat
        first = not self.chat.turns()
        answer = []
        try:
            async with aclosing(self.session.ask(text)) as events:  # saves the turn
                async for event in events:
                    frame = frame_for(event, self.session.interrupted)
                    if frame:
                        self.send(frame)
                    if isinstance(frame, p.TextDelta):
                        answer.append(frame.text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("chat turn failed")
            self.send(p.ChatError(message=f"{type(e).__name__}: {e}"))
        if first and self.chat.exists:
            self._write_title(self.chat, text, "".join(answer))

    def _write_title(self, chat: Chat, question: str, answer: str) -> None:
        """Title the chat in the background; until then (or if it fails) it keeps the question."""
        settings, release = self.library.settings, self.library.release

        async def write() -> None:
            try:
                title = await titles.write_title(question, answer, settings, release)
            except Exception:
                log.exception("could not write a title for chat %s", chat.id)
                return
            if chat.set_title(title, by_user=False):
                self.send(p.ChatTitle(chat_id=chat.id, title=title))

        task = asyncio.create_task(write())
        _titling.add(task)
        task.add_done_callback(_titling.discard)

    async def _close_session(self) -> None:
        if self.session:
            with contextlib.suppress(Exception):
                await self.session.disconnect()
            self.session = None


async def chat(ws: WebSocket, library: Library, requested: str | None) -> None:
    await ws.accept()
    try:
        opened = open_chat(library, requested)
    except ValueError as e:
        await ws.close(code=1008, reason=str(e))
        return
    await Connection(ws, library, opened).run()
