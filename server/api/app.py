"""The FastAPI app (plan step 10). `server.cli serve` runs it with uvicorn.

| Route | What it does |
|---|---|
| `POST /video?name=<file name>` | Raw-body upload; replaces the current video, starts ingest |
| `GET /video/current/status` | `IngestStatus` (also pushed over `WS /chat`) |
| `GET /video/current/proxy` | The 720p `proxy.mp4`, with HTTP range support |
| `GET /video/current/thumbs/<shot>.jpg` | A shot's thumbnail, made on first request |
| `WS /chat` | The chat; frames in `server.api.protocol` |
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from server import ingest
from server.agent.tools import ToolError, VideoTools
from server.api import chat
from server.api.library import Library, UploadError
from server.api.protocol import IngestStatus
from server.config import Release, Settings, load_release
from server.ingest.proxy import PROXY
from server.ingest.shots import SHOTS
from server.workspace import Workspace

THUMBS = "thumbs"


def create_app(settings: Settings | None = None, release: Release | None = None) -> FastAPI:
    library = Library(settings or Settings.load(), release or load_release())
    app = FastAPI(title="video-agent-harness")
    app.state.library = library

    def current() -> Workspace:
        if library.current is None:
            raise HTTPException(404, "no video uploaded yet")
        return library.current

    def ready_file(name: str) -> Workspace:
        workspace = current()
        if not workspace.path(name).is_file():
            raise HTTPException(409, f"{name} is not ready yet")
        return workspace

    @app.post("/video", response_model=IngestStatus)
    async def upload(request: Request, name: str) -> IngestStatus:
        try:
            video = await library.receive(name, request.stream())
            await run_in_threadpool(library.replace, video, name)
        except (UploadError, ingest.Rejected) as e:
            raise HTTPException(422, str(e)) from e
        return library.status()

    @app.get("/video/current/status", response_model=IngestStatus)
    def status() -> IngestStatus:
        current()
        return library.status()

    @app.get("/video/current/proxy")
    def proxy() -> FileResponse:
        return FileResponse(ready_file(PROXY).path(PROXY), media_type="video/mp4")

    @app.get("/video/current/thumbs/{shot}.jpg")
    def thumb(shot: int) -> Response:
        workspace = ready_file(SHOTS)
        path = workspace.path(f"{THUMBS}/{shot}.jpg")
        if not path.is_file():
            shots = workspace.read_json(SHOTS)["shots"]
            if not 0 <= shot < len(shots):
                raise HTTPException(404, f"no shot {shot}")
            middle = (shots[shot]["start_s"] + shots[shot]["end_s"]) / 2
            try:
                _, frames = VideoTools(workspace, library.settings, library.release).get_frames(
                    [middle]
                )
            except ToolError as e:
                raise HTTPException(500, str(e)) from e
            path.parent.mkdir(exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(frames[0])
            tmp.replace(path)
        return FileResponse(path, media_type="image/jpeg")

    @app.websocket("/chat")
    async def chat_socket(ws: WebSocket) -> None:
        await chat.chat(ws, library)

    return app
