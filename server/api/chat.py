"""`WS /chat`: the step 5 chat session over a WebSocket.

Each connection reads client frames in one task and sends every server frame
through one queue, so a turn can stream while an `interrupt` arrives and
`ingest_status` frames go out whenever `status.json` changes. A connection keeps
one `ChatSession` for the current video and resumes its stored session; after any
upload (even of the same video), the next message starts a new session. Each turn
is appended to the workspace's `chat.json`, which a new connection gets first as
a `History` frame, so the chat survives a page reload.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from server.agent.session import ChatSession, Event
from server.api import protocol as p
from server.api.library import HISTORY, Library
from server.auth import AuthMode, detect_auth
from server.workspace import Workspace

STATUS_POLL_S = 0.5

log = logging.getLogger(__name__)


def load_history(workspace: Workspace) -> list[p.ChatTurn]:
    if not workspace.path(HISTORY).is_file():
        return []
    return [p.ChatTurn.model_validate(t) for t in workspace.read_json(HISTORY)["turns"]]


def save_history(workspace: Workspace, turns: list[p.ChatTurn]) -> None:
    workspace.write_json(HISTORY, {"turns": [t.model_dump() for t in turns]})


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

    def refuse(self, message: str) -> None:
        """No turn started for the user's message."""
        self.send(p.ChatError(message=message, refused=True))

    async def run(self) -> None:
        current = self.library.current
        turns = load_history(current) if current else []
        self.send(
            p.History(
                video_id=current.id if current else None,
                generation=self.library.generation,
                turns=turns,
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
                    self.interrupted = True
                    await self.session.interrupt()
            elif self.turn and not self.turn.done():
                self.refuse("a turn is already running; interrupt it first")
            else:
                await self._start_turn(frame.text)

    async def _start_turn(self, text: str) -> None:
        status = self.library.status()
        if not status.chat_ready:
            self.refuse("the video is not chat-ready yet")
            return
        settings = self.library.settings
        if detect_auth(settings).mode is AuthMode.NONE and not settings.fake_providers:
            self.refuse("no Claude auth: set ANTHROPIC_API_KEY or log in")
            return
        try:
            await self._ensure_session()
        except Exception as e:
            log.exception("could not start a chat session")
            self.refuse(f"could not start the chat: {type(e).__name__}: {e}")
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
        workspace = self.session.workspace
        answer, tools = [], {}
        try:
            async for event in self.session.ask(text):
                frame = frame_for(event, self.interrupted)
                if frame:
                    self.send(frame)
                if isinstance(frame, p.TextDelta):
                    answer.append(frame.text)
                elif isinstance(frame, p.ToolUse):
                    tools[frame.id] = p.ToolCall(id=frame.id, name=frame.name, input=frame.input)
                elif isinstance(frame, p.ToolResult) and frame.id in tools:
                    tools[frame.id] = tools[frame.id].model_copy(
                        update={"result": frame.text, "is_error": frame.is_error}
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("chat turn failed")
            self.send(p.ChatError(message=f"{type(e).__name__}: {e}"))
        finally:  # also for an interrupted or abandoned turn
            self._save_turn(workspace, text, answer, tools)

    def _save_turn(
        self, workspace: Workspace, text: str, answer: list[str], tools: dict[str, p.ToolCall]
    ) -> None:
        if self.library.generation != self.generation:
            return  # the video was uploaded again mid-turn: that chat is gone
        if not answer and not tools:
            return  # nothing came back (a failed turn): the client drops it too
        reply = p.ChatTurn(
            role="assistant",
            text="".join(answer),
            tools=list(tools.values()),
            interrupted=self.interrupted,
        )
        user = p.ChatTurn(role="user", text=text)
        if workspace.dir.is_dir():
            save_history(workspace, [*load_history(workspace), user, reply])

    async def _close_session(self) -> None:
        if self.session:
            with contextlib.suppress(Exception):
                await self.session.disconnect()
            self.session = None


async def chat(ws: WebSocket, library: Library) -> None:
    await ws.accept()
    await Connection(ws, library).run()
