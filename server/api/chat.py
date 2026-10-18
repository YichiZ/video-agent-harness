"""`WS /chat`: the step 5 chat session over a WebSocket.

Each connection reads client frames in one task and sends every server frame
through one queue, so a turn can stream while an `interrupt` arrives and
`ingest_status` frames go out whenever `status.json` changes. A connection keeps
one `ChatSession` for the current video and resumes its stored session; after any
upload (even of the same video), the next message starts a new session.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from server.agent.session import ChatSession, Event
from server.api import protocol as p
from server.api.library import Library
from server.auth import AuthMode, detect_auth

STATUS_POLL_S = 0.5

log = logging.getLogger(__name__)


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
    def __init__(self, ws: WebSocket, library: Library):
        self.ws = ws
        self.library = library
        self.outbox: asyncio.Queue[BaseModel] = asyncio.Queue()
        self.session: ChatSession | None = None
        self.generation = -1  # the library's upload count the session belongs to
        self.turn: asyncio.Task | None = None
        self.interrupted = False

    def send(self, frame: BaseModel) -> None:
        self.outbox.put_nowait(frame)

    async def run(self) -> None:
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
                    self.interrupted = True
                    await self.session.interrupt()
            elif self.turn and not self.turn.done():
                self.send(p.ChatError(message="a turn is already running; interrupt it first"))
            else:
                await self._start_turn(frame.text)

    async def _start_turn(self, text: str) -> None:
        status = self.library.status()
        if not status.chat_ready:
            self.send(p.ChatError(message="the video is not chat-ready yet"))
            return
        settings = self.library.settings
        if detect_auth(settings).mode is AuthMode.NONE and not settings.fake_providers:
            self.send(p.ChatError(message="no Claude auth: set ANTHROPIC_API_KEY or log in"))
            return
        try:
            await self._ensure_session()
        except Exception as e:
            log.exception("could not start a chat session")
            self.send(p.ChatError(message=f"could not start the chat: {type(e).__name__}: {e}"))
            return
        self.interrupted = False
        self.turn = asyncio.create_task(self._turn(text))

    async def _ensure_session(self) -> None:
        current, generation = self.library.current, self.library.generation
        if self.session and self.generation == generation:
            return
        await self._close_session()
        if current is None:
            raise LookupError("no video")
        session = ChatSession(
            current,
            self.library.settings,
            self.library.release,
            resume=ChatSession.has_stored_session(current),
        )
        await session.connect()
        self.session, self.generation = session, generation

    async def _turn(self, text: str) -> None:
        assert self.session
        try:
            async for event in self.session.ask(text):
                frame = frame_for(event, self.interrupted)
                if frame:
                    self.send(frame)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("chat turn failed")
            self.send(p.ChatError(message=f"{type(e).__name__}: {e}"))

    async def _close_session(self) -> None:
        if self.session:
            with contextlib.suppress(Exception):
                await self.session.disconnect()
            self.session = None


async def chat(ws: WebSocket, library: Library) -> None:
    await ws.accept()
    await Connection(ws, library).run()
